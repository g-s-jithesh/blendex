import json
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from backend.app import VERSION, create_app

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def running_blendex() -> bool | None:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError):
        return None


def open_when_ready(server: uvicorn.Server) -> None:
    while not server.started:
        time.sleep(0.1)
    webbrowser.open(URL)


def main() -> None:
    state = running_blendex()
    if state:
        print(f"Blendex is already running at {URL}, opening it.")
        webbrowser.open(URL)
        return
    if state is False:
        raise RuntimeError(f"port {PORT} is used by another program; close it and start Blendex again")
    print(f"Blendex {VERSION} is running at {URL}\nKeep this window open while you use it; close it to quit.",
          flush=True)
    server = uvicorn.Server(uvicorn.Config(create_app(web_dir=str(BUNDLE / "web")), host=HOST, port=PORT,
                                           log_level="warning"))
    threading.Thread(target=open_when_ready, args=(server,), daemon=True).start()
    server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"Blendex could not start: {e}")
        input("Press Enter to close.")
