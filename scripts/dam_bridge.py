bl_info = {
    "name": "Blendex DAM Bridge",
    "author": "Blendex",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "category": "Import-Export",
    "description": "Lets the Blendex asset manager import files into this running Blender session",
}

import json
import os
import queue
import secrets
import socketserver
import threading
from pathlib import Path

import bpy

IMPORTERS = {
    ".obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
    ".fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
    ".glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
    ".ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    ".usd": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usda": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usdc": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usdz": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".abc": lambda p: bpy.ops.wm.alembic_import(filepath=p),
}
MAX_REQUEST = 64 * 1024
REPLY_TIMEOUT = 60.0

_requests: queue.Queue = queue.Queue()
_state: dict = {}


def sessions_dir() -> Path:
    if env := os.environ.get("DAM_SESSIONS"):
        return Path(env)
    base = Path(os.environ["APPDATA"]) if os.name == "nt" else Path.home() / ".local" / "share"
    return base / "dam" / "sessions"


def append_blend(path: str) -> None:
    with bpy.data.libraries.load(path) as (src, dst):
        dst.objects = list(src.objects)
    target = bpy.context.view_layer.active_layer_collection.collection
    for obj in dst.objects:
        if obj is not None:
            target.objects.link(obj)


def import_file(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext != ".blend" and ext not in IMPORTERS:
        raise ValueError(f"cannot import {ext or 'files without an extension'} into Blender")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    before = set(bpy.data.objects)
    append_blend(path) if ext == ".blend" else IMPORTERS[ext](path)
    return sorted(o.name for o in set(bpy.data.objects) - before)


def handle(request: dict) -> dict:
    if request.get("cmd") == "info":
        return {"ok": True, "pid": os.getpid(), "version": ".".join(map(str, bpy.app.version[:2])),
                "file": bpy.data.filepath or None}
    if request.get("cmd") == "import":
        imported = import_file(str(request.get("path", "")))
        bpy.ops.ed.undo_push(message="Blendex import")
        return {"ok": True, "imported": imported}
    return {"ok": False, "error": f"unknown command {request.get('cmd')!r}"}


def pump() -> float:
    while not _requests.empty():
        request, reply = _requests.get_nowait()
        try:
            reply.put(handle(request))
        except Exception as e:  # noqa: BLE001
            reply.put({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return 0.1


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline(MAX_REQUEST))
            if not isinstance(request, dict) or not secrets.compare_digest(str(request.get("token")), _state["token"]):
                raise PermissionError("bad token")
        except (ValueError, PermissionError) as e:
            return self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode() + b"\n")
        reply: queue.Queue = queue.Queue()
        _requests.put((request, reply))
        try:
            answer = reply.get(timeout=REPLY_TIMEOUT)
        except queue.Empty:
            answer = {"ok": False, "error": "Blender is busy; try again when it is idle"}
        self.wfile.write(json.dumps(answer).encode() + b"\n")


def register() -> None:
    if bpy.app.background or "server" in _state:
        return
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = secrets.token_hex(16)
    folder = sessions_dir()
    folder.mkdir(parents=True, exist_ok=True)
    session_file = folder / f"blender-{os.getpid()}.json"
    session_file.write_text(json.dumps({"app": "blender", "pid": os.getpid(), "port": server.server_address[1],
                                        "token": token}), encoding="utf-8")
    _state.update(server=server, token=token, file=session_file)
    bpy.app.timers.register(pump, persistent=True)


def unregister() -> None:
    if server := _state.pop("server", None):
        server.shutdown()
        server.server_close()
    if session_file := _state.pop("file", None):
        session_file.unlink(missing_ok=True)
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)


if __name__ == "__main__":
    register()
