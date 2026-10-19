import ctypes
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import webview

from backend.db import default_db_path

HOST, PORT = "127.0.0.1", int(os.environ.get("DAM_PORT") or 8765)
URL = f"http://{HOST}:{PORT}/"
TITLE = "Blendex"
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
LOG = Path(default_db_path()).parent / "blendex.log"
WEBVIEW_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Blendex" / "webview"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
BACKGROUND = "#0a0a0a"
LOADING = (f"<body style='margin:0;height:100vh;display:grid;place-content:center;justify-items:center;gap:20px;"
           f"background:{BACKGROUND};color:#e5e5e5;font:600 22px system-ui,sans-serif'>Blendex"
           "<div style='width:22px;height:22px;border:2px solid #404040;border-top-color:#a3a3a3;border-radius:50%;"
           "animation:spin 0.9s linear infinite'></div><style>@keyframes spin{to{transform:rotate(1turn)}}</style>")


def message(text: str, error: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)


def running_blendex() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=10) as r:
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return False


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


# Binding first is instant; probing a free port over HTTP takes ~2 s on Windows before the refusal comes back.
def claim_port() -> socket.socket | None:
    sock = socket.socket()
    try:
        sock.bind((HOST, PORT))
        sock.listen()
    except OSError:
        sock.close()
        return None
    return sock


def serve(sock: socket.socket) -> None:
    window = webview.create_window(TITLE, html=LOADING, width=1440, height=900, min_size=(800, 560),
                                   background_color=BACKGROUND)
    running: list = []
    failed: list[Exception] = []

    def boot() -> None:
        try:
            import uvicorn

            from backend.app import VERSION, create_app

            server = uvicorn.Server(uvicorn.Config(create_app(web_dir=str(BUNDLE / "web")), log_level="warning"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            running[:] = [server, thread]
            while not server.started:
                if not thread.is_alive():
                    raise RuntimeError("the server stopped while starting")
                time.sleep(0.05)
            print(f"Blendex {VERSION} running at {URL}", flush=True)
            window.load_url(URL)
        except Exception as e:  # noqa: BLE001
            failed.append(e)
            window.destroy()

    try:
        webview.start(boot, gui="edgechromium", private_mode=False, storage_path=str(WEBVIEW_DATA))
    except Exception as e:
        raise RuntimeError(f"the Microsoft Edge WebView2 Runtime is missing or broken ({e}).\n"
                           f"Install it from {WEBVIEW2_URL} and start Blendex again") from e
    finally:
        if running:
            running[0].should_exit = True
            running[1].join(timeout=30)
    if failed:
        raise failed[0]


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sock = claim_port()
    if sock:
        serve(sock)
    elif running_blendex():
        if not focus_running_window():
            message(f"Blendex is already running at {URL}")
    else:
        raise RuntimeError(f"port {PORT} is used by another program; close it and start Blendex again")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        message(f"Blendex could not start:\n\n{e}\n\nDetails are in {LOG}", error=True)
