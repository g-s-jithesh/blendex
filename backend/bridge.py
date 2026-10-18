import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from .dcc import BLENDER_IMPORTS

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
IMPORT_EXTS = ["blend", *BLENDER_IMPORTS]
SESSION_ID = re.compile(r"blender-\d+")
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


def list_sessions() -> list[dict]:
    found = []
    for path in sorted(sessions_dir().glob("blender-*.json")):
        session = _read(path)
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
            found.append({"id": path.stem, "app": "Blender", "version": info.get("version"),
                          "pid": info.get("pid"), "file": info.get("file"), "exts": IMPORT_EXTS})
        else:
            path.unlink(missing_ok=True)
    return found


def import_into(session_id: str, path: str) -> list[str]:
    session = _read(sessions_dir() / f"{session_id}.json") if SESSION_ID.fullmatch(session_id) else None
    if session is None:
        raise LookupError(session_id)
    try:
        reply = _ask(session, {"cmd": "import", "path": str(Path(path))}, IMPORT_TIMEOUT)
    except (OSError, ValueError) as e:
        raise BridgeError(f"Blender session did not answer: {e}") from e
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


def install(executable: str) -> None:
    result = subprocess.run([executable, "-b", "--python-exit-code", "1", "-P", str(SCRIPTS / "install_dam_bridge.py")],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        lines = [ln for ln in (result.stderr + result.stdout).splitlines() if "Error" in ln]
        raise BridgeError(lines[-1] if lines else f"Blender exited with code {result.returncode}")
