import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from backend.app import VERSION, create_app
from backend.db import default_db_path

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
LOG = Path(default_db_path()).parent / "blendex.log"
WINDOW_PROFILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Blendex" / "window"


def message(text: str, error: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Blendex", 0x10 if error else 0x40)


def running_blendex() -> bool | None:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return None


def app_browser() -> str | None:
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel in (("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe")):
        for root in filter(None, roots):
            if (exe := Path(root, *rel)).is_file():
                return str(exe)
    return None


def window_profile_in_use() -> bool:
    try:
        (WINDOW_PROFILE / "lockfile").unlink(missing_ok=True)
    except PermissionError:
        return True
    return False


def open_window() -> subprocess.Popen | None:
    exe = app_browser()
    if not exe:
        webbrowser.open(URL)
        return None
    return subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={WINDOW_PROFILE}", "--window-size=1440,900",
                             "--no-first-run", "--no-default-browser-check", "--disable-background-mode"])


def serve() -> None:
    server = uvicorn.Server(uvicorn.Config(create_app(web_dir=str(BUNDLE / "web")), host=HOST, port=PORT,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError(f"the server stopped while starting; port {PORT} may be in use")
        time.sleep(0.1)
    print(f"Blendex {VERSION} running at {URL}", flush=True)
    # An Edge still running on our profile (left by a crashed Blendex) takes the window and our process exits at once.
    tracked = not window_profile_in_use()
    window = open_window()
    if window and tracked:
        window.wait()
    else:
        message(f"Blendex is open at {URL}\n\nClick OK to quit Blendex.")
    server.should_exit = True
    thread.join(timeout=30)


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    state = running_blendex()
    if state:
        open_window()
    elif state is False:
        raise RuntimeError(f"port {PORT} is used by another program; close it and start Blendex again")
    else:
        serve()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        message(f"Blendex could not start:\n\n{e}\n\nDetails are in {LOG}", error=True)
