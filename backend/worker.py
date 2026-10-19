import asyncio
import contextlib
import json
import logging
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import db, dcc
from .blend_preview import read_embedded_preview, rgba_to_png
from .bridge import mayapy
from .indexer import CONVERTIBLE, PROXY_EXTS

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "blender_export_proxy.py"
MAYA_SCRIPT = SCRIPTS / "maya_export_fbx.py"
MAYA_EXTS = {"ma", "mb"}
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
log = logging.getLogger(__name__)


def hide(path: Path) -> None:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        attrs = k32.GetFileAttributesW(str(path))
        if attrs != -1 and attrs != 0xFFFFFFFF:
            k32.SetFileAttributesW(str(path), attrs | 0x2)


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        # Windows venv python.exe and some launchers re-spawn the real process; kill the whole tree.
        k = await asyncio.create_subprocess_exec("taskkill", "/F", "/T", "/PID", str(proc.pid),
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await k.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()


def cache_dir(library_path: str) -> Path:
    cache = Path(library_path) / ".cache"
    cache.mkdir(exist_ok=True)
    if cache.resolve() != Path(library_path).resolve() / ".cache":
        raise PermissionError(f"{cache} links to another folder; Blendex will not write previews through it")
    hide(cache)
    return cache


def replace_retrying(src: str | Path, dst: Path) -> None:
    # Windows refuses to replace a file another handle has open, e.g. a thumbnail being served right now.
    for delay in REPLACE_RETRY_DELAYS:
        try:
            return os.replace(src, dst)
        except PermissionError:
            time.sleep(delay)
    os.replace(src, dst)


def write_embedded_preview(src: str, library_path: str, asset_id: int) -> bool:
    tmp = None
    try:
        cache = cache_dir(library_path)
        if not (preview := read_embedded_preview(src)):
            return False
        tmp = cache / f"{asset_id}.{secrets.token_hex(4)}.tmp.png"
        tmp.write_bytes(rgba_to_png(*preview))
        replace_retrying(tmp, cache / f"{asset_id}.png")
        return True
    except OSError:
        log.exception("writing embedded preview of asset %s failed", asset_id)
        return False
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def _read_meta(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def mayapy_command(conn: sqlite3.Connection) -> list[str] | None:
    exe = os.environ.get("DAM_MAYAPY")
    if not exe:
        mayas = [a for a in db.list_apps(conn) if a["id"].startswith("maya")]
        path = mayapy(max(mayas, key=dcc._version_key)["executable"]) if mayas else None
        exe = str(path) if path and path.is_file() else None
    if not exe:
        return None
    return [sys.executable, exe] if exe.lower().endswith(".py") else [exe]


def maya_app_dir(conn: sqlite3.Connection) -> Path:
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    return Path(db_file or db.default_db_path()).parent / "maya"


def _failure(meta: dict, rc: int | None, timeout: float, script: str) -> str:
    if meta.get("error"):
        return str(meta["error"])
    return f"timed out after {timeout:g}s" if rc is None else f"exit code {rc}" if rc else f"{script} wrote no output"


class Worker:
    def __init__(self, conn: sqlite3.Connection, publish: Callable[[str, dict | None], None],
                 on_queue: Callable[[dict], None]):
        self.conn, self.publish, self.on_queue = conn, publish, on_queue
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.queued: set[int] = set()
        self.active: dict[int, int | None] = {}
        self.running = 0
        self.unpaused = asyncio.Event()
        self.unpaused.set()
        self.sent: dict | None = None
        self.flush: asyncio.TimerHandle | None = None
        self.procs: set[asyncio.subprocess.Process] = set()
        self.concurrency = int(os.environ.get("DAM_WORKERS") or max(1, (os.cpu_count() or 1) // 4))
        self.timeout = float(os.environ.get("DAM_JOB_TIMEOUT") or 600)
        self.tasks: list[asyncio.Task] = []
        self.maya_warm = False
        self.maya_first = asyncio.Lock()

    def busy(self) -> bool:
        return bool(self.running or (self.queued and self.unpaused.is_set()))

    def state(self) -> dict:
        return {"queued": len(self.queued), "running": self.running, "paused": not self.unpaused.is_set()}

    def set_paused(self, paused: bool) -> dict:
        if paused:
            self.unpaused.clear()
        else:
            self.unpaused.set()
        self._changed()
        return self.state()

    def _changed(self) -> None:
        if not self.flush:
            # Coalesce a burst (e.g. a scan queueing thousands of assets) into one queue.updated event.
            self.flush = asyncio.get_running_loop().call_later(0.1, self._send_state)

    def _send_state(self) -> None:
        self.flush = None
        if (state := self.state()) != self.sent:
            self.sent = state
            self.on_queue(state)

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(self.concurrency)]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.flush:
            self.flush.cancel()

    def enqueue(self, asset_id: int) -> None:
        converting_this_version = self.active.get(asset_id, -1) == db.current_version_id(self.conn, asset_id)
        if asset_id not in self.queued and not converting_this_version:
            self.queued.add(asset_id)
            self.queue.put_nowait(asset_id)
            self._changed()

    async def route(self, asset_id: int, event: str | None = None, convert: bool = True) -> None:
        asset = db.get_asset(self.conn, asset_id)
        if not asset or asset["ext"] not in CONVERTIBLE:
            return
        if asset["ext"] == "blend":
            vid = db.current_version_id(self.conn, asset_id)
            lib_path = db.get_library(self.conn, asset["library_id"])["path"]
            shown = await asyncio.to_thread(write_embedded_preview, asset["path"], lib_path, asset_id)
            if shown and db.current_version_id(self.conn, asset_id) == vid:
                db.set_thumbnail(self.conn, vid)
                self.conn.commit()
        if event:
            self.publish(event, db.get_asset(self.conn, asset_id))
        if convert:
            self.enqueue(asset_id)

    async def _loop(self) -> None:
        while True:
            asset_id = await self.queue.get()
            await self.unpaused.wait()
            self.queued.discard(asset_id)
            self.active[asset_id] = db.current_version_id(self.conn, asset_id)
            self.running += 1
            self._changed()
            try:
                await self.convert(asset_id)
            except Exception as e:
                log.exception("conversion of asset %s crashed", asset_id)
                self.conn.rollback()
                if vid := db.current_version_id(self.conn, asset_id):
                    db.set_status(self.conn, vid, "failed", f"{type(e).__name__}: {e}")
                    self._emit("conversion.failed", asset_id)
            finally:
                self.active.pop(asset_id, None)
                self.running -= 1
                self._changed()

    def _emit(self, event: str, asset_id: int) -> None:
        self.conn.commit()
        self.publish(event, db.get_asset(self.conn, asset_id))

    def _fail(self, asset_id: int, vid: int, error: str) -> None:
        if db.current_version_id(self.conn, asset_id) == vid:
            db.set_status(self.conn, vid, "failed", error)
            self._emit("conversion.failed", asset_id)

    async def _export_fbx(self, cmd: list[str], src: str, fbx: str, meta_path: str) -> str | None:
        if self.maya_warm:
            return await self._mayapy(cmd, src, fbx, meta_path)
        # Two mayapy processes initialising a fresh MAYA_APP_DIR at once make one fail, so the first runs alone.
        async with self.maya_first:
            error = await self._mayapy(cmd, src, fbx, meta_path)
            self.maya_warm = self.maya_warm or error is None
            return error

    async def _mayapy(self, cmd: list[str], src: str, fbx: str, meta_path: str) -> str | None:
        app_dir = maya_app_dir(self.conn)
        app_dir.mkdir(parents=True, exist_ok=True)
        # A dedicated MAYA_APP_DIR keeps the user's Maya prefs, userSetup and plug-in autoloads out of the job.
        rc = await self._run([*cmd, str(MAYA_SCRIPT), "--input", src, "--out-fbx", fbx, "--out-meta", meta_path],
                             os.environ | {"MAYA_APP_DIR": str(app_dir)})
        meta = _read_meta(meta_path)
        if rc == 0 and meta.get("ok") and os.path.isfile(fbx):
            return None
        return _failure(meta, rc, self.timeout, "Maya export script")

    async def convert(self, asset_id: int) -> None:
        asset = db.get_asset(self.conn, asset_id)
        if not asset or asset["ext"] not in CONVERTIBLE:
            return
        vid = db.current_version_id(self.conn, asset_id)
        cmd = dcc.blender_command(self.conn)
        maya_cmd = mayapy_command(self.conn) if asset["ext"] in MAYA_EXTS else None
        if not cmd:
            return self._fail(asset_id, vid, "Blender not found; install Blender or set DAM_BLENDER")
        if asset["ext"] in MAYA_EXTS and not maya_cmd:
            return self._fail(asset_id, vid, "Maya not found; install Maya or set DAM_MAYAPY")
        db.set_status(self.conn, vid, "converting")
        self._emit("conversion.started", asset_id)
        full = asset["ext"] in PROXY_EXTS
        cache = cache_dir(db.get_library(self.conn, asset["library_id"])["path"])
        tmp = cache / f"{asset_id}.{secrets.token_hex(4)}.tmp"
        glb, png, meta_path = f"{tmp}.glb", f"{tmp}.png", f"{tmp}.json"
        fbx, maya_meta = f"{tmp}.fbx", f"{tmp}.maya.json"
        try:
            src = asset["path"]
            if maya_cmd:
                if error := await self._export_fbx(maya_cmd, src, fbx, maya_meta):
                    return self._fail(asset_id, vid, error)
                src = fbx
            rc = await self._run([*cmd, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(SCRIPT),
                                  "--", "--input", src, "--out-png", png, "--out-meta", meta_path,
                                  *(["--out-glb", glb] if full else [])])
            meta = _read_meta(meta_path)
            if db.current_version_id(self.conn, asset_id) != vid:
                return
            if rc == 0 and meta.get("ok") and os.path.isfile(png) and (not full or os.path.isfile(glb)):
                await asyncio.to_thread(replace_retrying, png, cache / f"{asset_id}.png")
                if full:
                    await asyncio.to_thread(replace_retrying, glb, cache / f"{asset_id}.glb")
                db.set_result(self.conn, vid, meta.get("poly_count"), meta.get("proxy_poly_count") if full else None,
                              full)
                return self._emit("conversion.done", asset_id)
            self._fail(asset_id, vid, _failure(meta, rc, self.timeout, "proxy script"))
        finally:
            for f in (glb, png, meta_path, fbx, maya_meta):
                with contextlib.suppress(OSError):
                    os.remove(f)

    async def _run(self, argv: list[str], env: dict[str, str] | None = None) -> int | None:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        proc = await asyncio.create_subprocess_exec(*argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL, env=env, **kw)
        self.procs.add(proc)
        try:
            return await asyncio.wait_for(proc.wait(), self.timeout)
        except TimeoutError:
            return None
        finally:
            if proc.returncode is None:
                await kill_tree(proc)
            self.procs.discard(proc)
