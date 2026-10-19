import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path

from .dcc import BLENDER_IMPORTS

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
IMPORT_EXTS = ["blend", *BLENDER_IMPORTS]
MAYA_IMPORT_EXTS = ["ma", "mb", "obj", "fbx", "dae", "usd", "usda", "usdc", "usdz", "abc", "stl"]
SESSION_APPS = {"blender": ("Blender", IMPORT_EXTS), "maya": ("Maya", MAYA_IMPORT_EXTS)}
SESSION_ID = re.compile(r"(blender|maya)-\d+")
FOLDERID_DOCUMENTS = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
CONNECT_TIMEOUT = 3.0
INFO_TIMEOUT = 2.0
IMPORT_TIMEOUT = 75.0
MAX_REPLY = 64 * 1024


class BridgeError(Exception):
    pass


def sessions_dir() -> Path:
    if env := os.environ.get("DAM_SESSIONS"):
        return Path(env)
    base = Path(os.environ["APPDATA"]) if os.name == "nt" else Path.home() / ".local" / "share"
    return base / "dam" / "sessions"


def _read(path: Path) -> dict | None:
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(s, dict) and isinstance(s.get("port"), int) and isinstance(s.get("token"), str)
    return s if ok else None


def _ask(session: dict, payload: dict, timeout: float) -> dict:
    # Windows reports a refused localhost connection only after ~2 s of SYN retries.
    with socket.create_connection(("127.0.0.1", session["port"]), timeout=CONNECT_TIMEOUT) as conn:
        conn.settimeout(timeout)
        conn.sendall(json.dumps(payload | {"token": session["token"]}).encode() + b"\n")
        reply = json.loads(conn.makefile("rb").readline(MAX_REPLY))
    return reply if isinstance(reply, dict) else {}


def session_app(session_id: str) -> tuple[str, list[str]] | None:
    m = SESSION_ID.fullmatch(session_id)
    return SESSION_APPS[m[1]] if m else None


def list_sessions() -> list[dict]:
    found = []
    paths = [p for prefix in SESSION_APPS for p in sessions_dir().glob(f"{prefix}-*.json")]
    for path in sorted(paths):
        app = session_app(path.stem)
        session = _read(path) if app else None
        try:
            if session is None:
                raise ConnectionRefusedError
            info = _ask(session, {"cmd": "info"}, INFO_TIMEOUT)
        except (ConnectionRefusedError, ValueError):
            path.unlink(missing_ok=True)
            continue
        except OSError:
            continue
        if info.get("ok"):
            found.append({"id": path.stem, "app": app[0], "version": info.get("version"),
                          "pid": info.get("pid"), "file": info.get("file"), "exts": app[1]})
        else:
            path.unlink(missing_ok=True)
    return found


def import_into(session_id: str, path: str) -> list[str]:
    app = session_app(session_id)
    session = _read(sessions_dir() / f"{session_id}.json") if app else None
    if app is None or session is None:
        raise LookupError(session_id)
    try:
        reply = _ask(session, {"cmd": "import", "path": str(Path(path))}, IMPORT_TIMEOUT)
    except (OSError, ValueError) as e:
        raise BridgeError(f"{app[0]} session did not answer: {e}") from e
    if not reply.get("ok"):
        raise BridgeError(str(reply.get("error") or "import failed"))
    return [str(n) for n in reply.get("imported", [])]


def addon_path(version: str) -> Path:
    if env := os.environ.get("BLENDER_USER_RESOURCES"):
        return Path(env) / "scripts" / "addons" / "dam_bridge.py"
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) / "Blender Foundation" / "Blender"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Blender"
    else:
        base = Path.home() / ".config" / "blender"
    return base / version / "scripts" / "addons" / "dam_bridge.py"


def _documents() -> Path:
    if sys.platform != "win32":
        return Path.home() / "Documents"
    # Documents may be redirected (OneDrive), so %USERPROFILE%\Documents is not where Maya looks.
    out = ctypes.c_wchar_p()
    ctypes.oledll.shell32.SHGetKnownFolderPath(ctypes.create_string_buffer(FOLDERID_DOCUMENTS.bytes_le), 0, None,
                                               ctypes.byref(out))
    try:
        return Path(out.value or "")
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def maya_plugin_path(version: str) -> Path:
    if env := os.environ.get("MAYA_APP_DIR"):
        base = Path(env)
    elif sys.platform == "win32":
        base = _documents() / "maya"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences" / "Autodesk" / "maya"
    else:
        base = Path.home() / "maya"
    return base / version / "plug-ins" / "maya_bridge.py"


def mayapy(executable: str) -> Path:
    exe = Path(executable)
    if sys.platform == "win32":
        return exe.with_name("mayapy.exe")
    return exe.parent.parent / "bin" / "mayapy" if sys.platform == "darwin" else exe.with_name("mayapy")


def install(executable: str) -> None:
    if "maya" in Path(executable).name.lower():
        name, timeout, env = "Maya", 180, os.environ | {"MAYA_SKIP_USERSETUP_PY": "1"}
        argv = [str(mayapy(executable)), str(SCRIPTS / "install_maya_bridge.py")]
    else:
        name, timeout, env = "Blender", 120, None
        argv = [executable, "-b", "--python-exit-code", "1", "-P", str(SCRIPTS / "install_dam_bridge.py")]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                            env=env, check=False)
    if result.returncode != 0:
        lines = [ln for ln in (result.stderr + result.stdout).splitlines() if "Error" in ln]
        raise BridgeError(lines[-1] if lines else f"{name} exited with code {result.returncode}")
