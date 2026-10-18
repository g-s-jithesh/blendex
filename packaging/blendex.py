import ctypes
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import uvicorn
import webview

from backend.app import VERSION, create_app
from backend.db import default_db_path

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"
TITLE = "Blendex"
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
LOG = Path(default_db_path()).parent / "blendex.log"
WEBVIEW_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Blendex" / "webview"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def message(text: str, error: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)


def running_blendex() -> bool | None:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return None


def focus_running_window() -> bool:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.FindWindowW(None, TITLE)
    while hwnd:
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        path = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(path))
        found = handle and kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))
        if handle:
            kernel32.CloseHandle(handle)
        if found and os.path.normcase(path.value) == os.path.normcase(sys.executable):
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return True
        hwnd = user32.FindWindowExW(None, hwnd, None, TITLE)
    return False


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
    try:
        webview.create_window(TITLE, URL, width=1440, height=900, min_size=(800, 560))
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(WEBVIEW_DATA))
    except Exception as e:
        raise RuntimeError(f"the Microsoft Edge WebView2 Runtime is missing or broken ({e}).\n"
                           f"Install it from {WEBVIEW2_URL} and start Blendex again") from e
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    state = running_blendex()
    if state:
        if not focus_running_window():
            message(f"Blendex is already running at {URL}")
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
