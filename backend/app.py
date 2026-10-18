import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from . import bridge, db, dcc
from .indexer import CONVERTIBLE, PROXY_EXTS, Indexer
from .worker import Worker

VERSION = "1.1.0"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class LibraryCreate(BaseModel):
    path: str = Field(min_length=1)


class LaunchRequest(BaseModel):
    asset_id: int
    app_id: str


class ImportRequest(BaseModel):
    asset_id: int


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(db_path: str | None = None, web_dir: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = app.state
        s.conn = db.connect(db_path or db.default_db_path())
        s.clients = set()

        def publish(event: str, asset: dict | None) -> None:
            if asset:
                for q in s.clients:
                    q.put_nowait({"type": event, "asset_id": asset["id"], "data": asset})

        s.worker = Worker(s.conn, publish)
        s.indexer = Indexer(s.conn, publish, s.worker.route)
        s.publish = publish

        async def idle() -> None:
            while s.indexer.busy() or s.worker.busy():
                await asyncio.sleep(0.05)

        s.idle = idle
        with s.conn:
            db.replace_apps(s.conn, dcc.detect_apps())
        s.worker.start()
        for lib in db.list_libraries(s.conn):
            s.indexer.start(lib)
        try:
            yield
        finally:
            s.indexer.stop()
            await s.worker.stop()
            s.conn.close()

    app = FastAPI(title="3D DAM daemon", version=VERSION, lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError) -> JSONResponse:
        return _error(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
        msg = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return _error(422, "VALIDATION_ERROR", msg or "invalid request")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, e: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if e.status_code == 404 else "VALIDATION_ERROR" if e.status_code < 500 else "INTERNAL"
        return _error(e.status_code, code, str(e.detail))

    @app.exception_handler(Exception)
    async def internal_error(_: Request, e: Exception) -> JSONResponse:
        return _error(500, "INTERNAL", f"{type(e).__name__}: {e}")

    def asset_or_404(asset_id: int) -> dict:
        if asset := db.get_asset(app.state.conn, asset_id):
            return asset
        raise ApiError(404, "NOT_FOUND", f"asset {asset_id} not found")

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "version": VERSION}

    @app.get("/api/libraries")
    async def list_libraries() -> list[dict]:
        return db.list_libraries(app.state.conn)

    @app.post("/api/libraries", status_code=201)
    async def create_library(body: LibraryCreate) -> dict:
        raw = Path(body.path).expanduser()
        try:
            p = raw.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            p = None
        if not raw.is_absolute() or p is None or not p.is_dir():
            raise ApiError(422, "INVALID_PATH", f"not an existing absolute directory: {body.path}")
        for lib in db.list_libraries(app.state.conn):
            other = Path(lib["path"])
            if other == p:
                raise ApiError(409, "ALREADY_EXISTS", f"library already registered: {lib['path']}")
            if p.is_relative_to(other) or other.is_relative_to(p):
                raise ApiError(422, "INVALID_PATH", f"overlaps library {lib['id']}: {lib['path']}")
        with app.state.conn:
            lib = db.add_library(app.state.conn, p.as_posix())
        app.state.indexer.start(lib)
        return lib

    @app.delete("/api/libraries/{library_id}", status_code=204)
    async def delete_library(library_id: int) -> Response:
        conn = app.state.conn
        lib = db.get_library(conn, library_id)
        if not lib:
            raise ApiError(404, "NOT_FOUND", f"library {library_id} not found")
        app.state.indexer.unwatch(library_id)
        with conn:
            removed = db.remove_assets(conn, lib["path"])
            db.delete_library(conn, library_id)
        for asset in removed:
            app.state.publish("asset.removed", asset)
        return Response(status_code=204)

    @app.get("/api/assets")
    async def list_assets(library_id: int | None = None, q: str | None = None, ext: str | None = None,
                          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
        items, total = db.list_assets(app.state.conn, library_id, q, ext, limit, offset)
        return {"items": items, "total": total}

    @app.get("/api/assets/{asset_id}")
    async def get_asset(asset_id: int) -> dict:
        return asset_or_404(asset_id)

    async def serve_cache(asset_id: int, suffix: str, media_type: str, exts: set[str]) -> FileResponse:
        conn = app.state.conn
        asset = asset_or_404(asset_id)
        if asset["ext"] in exts:
            cache = Path(db.get_library(conn, asset["library_id"])["path"]).resolve() / ".cache"
            f = (cache / f"{asset_id}{suffix}").resolve()
            if f.is_relative_to(cache) and f.is_file():
                return FileResponse(f, media_type=media_type)
            if asset["status"] == "ready":
                with conn:
                    db.clear_cache_flags(conn, db.current_version_id(conn, asset_id))
                await app.state.worker.route(asset_id, "asset.updated")
        raise ApiError(404, "NOT_FOUND", f"no cached {suffix[1:]} for asset {asset_id}")

    @app.get("/api/assets/{asset_id}/thumbnail")
    async def thumbnail(asset_id: int, v: str | None = None) -> FileResponse:
        return await serve_cache(asset_id, ".png", "image/png", CONVERTIBLE)

    @app.get("/api/assets/{asset_id}/proxy")
    async def proxy(asset_id: int, v: str | None = None) -> FileResponse:
        return await serve_cache(asset_id, ".glb", "model/gltf-binary", PROXY_EXTS)

    @app.post("/api/assets/{asset_id}/convert", status_code=202)
    async def convert(asset_id: int) -> Response:
        ext = asset_or_404(asset_id)["ext"]
        if ext not in CONVERTIBLE:
            raise ApiError(409, "UNSUPPORTED", f"cannot convert .{ext} files")
        await app.state.worker.route(asset_id, "asset.updated" if ext == "blend" else None)
        return Response(status_code=202)

    @app.get("/api/apps")
    async def list_apps() -> list[dict]:
        return db.list_apps(app.state.conn)

    @app.post("/api/launch", status_code=202)
    async def launch(body: LaunchRequest) -> Response:
        asset = asset_or_404(body.asset_id)
        dcc_app = db.get_app(app.state.conn, body.app_id)
        if not dcc_app:
            raise ApiError(404, "NOT_FOUND", f"app {body.app_id} not found")
        if asset["ext"] not in dcc_app["exts"]:
            raise ApiError(409, "UNSUPPORTED", f"{dcc_app['name']} does not open .{asset['ext']} files")
        if not Path(asset["path"]).is_file():
            raise ApiError(404, "NOT_FOUND", f"file missing on disk: {asset['path']}")
        try:
            dcc.launch(dcc_app, asset["path"])
        except OSError as e:
            raise ApiError(500, "LAUNCH_FAILED", f"{dcc_app['name']} failed to start: {e}") from e
        return Response(status_code=202)

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict]:
        return await asyncio.to_thread(bridge.list_sessions)

    @app.post("/api/sessions/{session_id}/import")
    async def import_into_session(session_id: str, body: ImportRequest) -> dict:
        asset = asset_or_404(body.asset_id)
        if asset["ext"] not in bridge.IMPORT_EXTS:
            raise ApiError(409, "UNSUPPORTED", f"Blender cannot import .{asset['ext']} files")
        if not Path(asset["path"]).is_file():
            raise ApiError(404, "NOT_FOUND", f"file missing on disk: {asset['path']}")
        try:
            imported = await asyncio.to_thread(bridge.import_into, session_id, asset["path"])
        except LookupError as e:
            raise ApiError(404, "NOT_FOUND", f"Blender session {session_id} is not running") from e
        except bridge.BridgeError as e:
            raise ApiError(502, "BRIDGE_FAILED", str(e)) from e
        return {"imported": imported}

    def blender_apps() -> list[dict]:
        return [a for a in db.list_apps(app.state.conn) if a["id"].startswith("blender") and a["version"]]

    @app.get("/api/bridges")
    async def list_bridges() -> list[dict]:
        return [{"app_id": a["id"], "name": a["name"], "installed": bridge.addon_path(a["version"]).is_file()}
                for a in blender_apps()]

    @app.post("/api/bridges/{app_id}", status_code=204)
    async def install_bridge(app_id: str) -> Response:
        target = next((a for a in blender_apps() if a["id"] == app_id), None)
        if not target:
            raise ApiError(404, "NOT_FOUND", f"no Blender install with id {app_id}")
        try:
            await asyncio.to_thread(bridge.install, target["executable"])
        except (bridge.BridgeError, OSError, subprocess.TimeoutExpired) as e:
            raise ApiError(502, "BRIDGE_FAILED", f"installing the bridge into {target['name']} failed: {e}") from e
        return Response(status_code=204)

    @app.websocket("/ws/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        app.state.clients.add(q)

        async def pump() -> None:
            while True:
                await ws.send_json(await q.get())

        task = asyncio.create_task(pump())
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            task.cancel()
            app.state.clients.discard(q)

    if web_dir and (Path(web_dir) / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
