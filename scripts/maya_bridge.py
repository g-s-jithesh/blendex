import json
import os
import queue
import secrets
import socketserver
import threading
from pathlib import Path

import maya.api.OpenMaya as om
import maya.utils

# Installed into <version>/scripts (on Maya's sys.path); from the repo it is the plug-in's sibling.
import maya_safety
from maya import cmds, mel

IMPORTERS = {
    ".ma": ("mayaAscii", None),
    ".mb": ("mayaBinary", None),
    ".obj": ("OBJ", "objExport"),
    ".fbx": ("FBX", "fbxmaya"),
    ".dae": ("DAE_FBX", "fbxmaya"),
    ".usd": ("USD Import", "mayaUsdPlugin"),
    ".usda": ("USD Import", "mayaUsdPlugin"),
    ".usdc": ("USD Import", "mayaUsdPlugin"),
    ".usdz": ("USD Import", "mayaUsdPlugin"),
    ".abc": ("Alembic", "AbcImport"),
    ".stl": ("STLImport", "stlTranslator"),
}
MAX_REQUEST = 64 * 1024
REPLY_TIMEOUT = 60.0

_state: dict = {}


def maya_useNewAPI() -> None:
    pass


def sessions_dir() -> Path:
    if env := os.environ.get("DAM_SESSIONS"):
        return Path(env)
    base = Path(os.environ["APPDATA"]) if os.name == "nt" else Path.home() / ".local" / "share"
    return base / "dam" / "sessions"


def import_file(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMPORTERS:
        raise ValueError(f"cannot import {ext or 'files without an extension'} into Maya")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    file_type, plugin = IMPORTERS[ext]
    if plugin and not cmds.pluginInfo(plugin, query=True, loaded=True):
        cmds.loadPlugin(plugin, quiet=True)
    before = set(cmds.ls(assemblies=True))
    # FBX's default "merge" mode overwrites same-named scene nodes instead of adding new ones.
    fbx_mode = mel.eval("FBXImportMode -q") if plugin == "fbxmaya" else None
    if fbx_mode:
        mel.eval("FBXImportMode -v add")
    try:
        with maya_safety.untrusted_scene(path):
            cmds.file(path, i=True, type=file_type, ignoreVersion=True, executeScriptNodes=False,
                      loadReferenceDepth="none")
    finally:
        if fbx_mode:
            mel.eval(f"FBXImportMode -v {fbx_mode}")
    return sorted(set(cmds.ls(assemblies=True)) - before)


def handle(request: dict) -> dict:
    if request.get("cmd") == "info":
        return {"ok": True, "pid": os.getpid(), "version": cmds.about(version=True),
                "file": cmds.file(query=True, sceneName=True) or None}
    if request.get("cmd") == "import":
        return {"ok": True, "imported": import_file(str(request.get("path", "")))}
    return {"ok": False, "error": f"unknown command {request.get('cmd')!r}"}


def run_on_main_thread(request: dict, reply: queue.Queue, expired: threading.Event) -> None:
    if expired.is_set():
        return
    try:
        reply.put(handle(request))
    except maya_safety.UnsafeScene as e:
        reply.put({"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        reply.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline(MAX_REQUEST))
            if not isinstance(request, dict) or not secrets.compare_digest(str(request.get("token")), _state["token"]):
                raise PermissionError("bad token")
        except (ValueError, PermissionError) as e:
            return self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode() + b"\n")
        reply: queue.Queue = queue.Queue()
        expired = threading.Event()
        maya.utils.executeDeferred(run_on_main_thread, request, reply, expired)
        try:
            answer = reply.get(timeout=REPLY_TIMEOUT)
        except queue.Empty:
            expired.set()
            answer = {"ok": False, "error": "Maya is busy; try again when it is idle"}
        self.wfile.write(json.dumps(answer).encode() + b"\n")


def stop(*_args) -> None:
    if server := _state.pop("server", None):
        server.shutdown()
        server.server_close()
    if session_file := _state.pop("file", None):
        session_file.unlink(missing_ok=True)


def initializePlugin(plugin: om.MObject) -> None:
    om.MFnPlugin(plugin, "Blendex", "1.0.0")
    if cmds.about(batch=True) or "server" in _state:
        return
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = secrets.token_hex(16)
    folder = sessions_dir()
    folder.mkdir(parents=True, exist_ok=True)
    session_file = folder / f"maya-{os.getpid()}.json"
    session_file.write_text(json.dumps({"app": "maya", "pid": os.getpid(), "port": server.server_address[1],
                                        "token": token}), encoding="utf-8")
    _state.update(server=server, token=token, file=session_file,
                  exit_cb=om.MSceneMessage.addCallback(om.MSceneMessage.kMayaExiting, stop))


def uninitializePlugin(plugin: om.MObject) -> None:
    if exit_cb := _state.pop("exit_cb", None):
        om.MMessage.removeCallback(exit_cb)
    stop()
