import hashlib
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import BLENDER, ROOT, add_library, assert_error, assert_shape, by_name, drain

from backend import bridge, dcc

TOKEN = "t" * 32
IMPORT_EXTS = ["blend", *dcc.BLENDER_IMPORTS]


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    folder = tmp_path / "sessions"
    folder.mkdir()
    monkeypatch.setenv("DAM_SESSIONS", str(folder))
    servers = []

    def start(pid=4242, reply=None, file_token=TOKEN):
        seen = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline())
                seen.append(req)
                if req.get("token") != TOKEN:
                    ans = {"ok": False, "error": "bad token"}
                elif req.get("cmd") == "info":
                    ans = {"ok": True, "pid": pid, "version": "5.2", "file": "C:/proj/house.blend"}
                else:
                    ans = reply or {"ok": True, "imported": ["Suzanne"]}
                self.wfile.write(json.dumps(ans).encode() + b"\n")

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        (folder / f"blender-{pid}.json").write_text(json.dumps(
            {"app": "blender", "pid": pid, "port": server.server_address[1], "token": file_token}))
        return seen

    yield folder, start
    for server in servers:
        server.shutdown()
        server.server_close()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_list_sessions_keeps_live_ones_and_deletes_dead_files(sessions):
    folder, start = sessions
    start()
    start(pid=7, file_token="wrong" * 6)
    (folder / "blender-1.json").write_text(json.dumps({"pid": 1, "port": closed_port(), "token": TOKEN}))
    (folder / "blender-2.json").write_text("not json")
    (folder / "notes.txt").write_text("ignored")
    got = bridge.list_sessions()
    assert got == [{"id": "blender-4242", "app": "Blender", "version": "5.2", "pid": 4242,
                    "file": "C:/proj/house.blend", "exts": IMPORT_EXTS}]
    assert sorted(p.name for p in folder.iterdir()) == ["blender-4242.json", "notes.txt"]


def test_import_into_forwards_native_path_and_reports_errors(sessions):
    _folder, start = sessions
    seen = start()
    assert bridge.import_into("blender-4242", "C:/lib/a b & c.obj") == ["Suzanne"]
    assert seen[-1] == {"cmd": "import", "path": str(Path("C:/lib/a b & c.obj")), "token": TOKEN}
    for bad in ("blender-1", "../blender-4242", "blender-4242/../x", "explorer", ""):
        with pytest.raises(LookupError):
            bridge.import_into(bad, "C:/lib/a.obj")
    start(pid=5, reply={"ok": False, "error": "RuntimeError: boom"})
    with pytest.raises(bridge.BridgeError, match="boom"):
        bridge.import_into("blender-5", "C:/lib/a.obj")


def test_sessions_api(daemon, lib, sessions):
    _folder, start = sessions
    seen = start()
    start(pid=5, reply={"ok": False, "error": "ValueError: nope"})
    write(lib / "m.obj", b"o")
    write(lib / "r.ma", b"ma")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        r = c.get("/api/sessions")
        assert r.status_code == 200
        assert_shape(r.json(), {"type": "array", "items": {"$ref": "Session"}})
        assert [s["id"] for s in r.json()] == ["blender-4242", "blender-5"]

        r = c.post("/api/sessions/blender-4242/import", json={"asset_id": got["m.obj"]["id"]})
        assert r.status_code == 200 and r.json() == {"imported": ["Suzanne"]}
        assert seen[-1]["path"] == str(Path(got["m.obj"]["path"]))
        assert_error(c.post("/api/sessions/blender-4242/import", json={"asset_id": got["r.ma"]["id"]}),
                     409, "UNSUPPORTED")
        assert_error(c.post("/api/sessions/blender-4242/import", json={"asset_id": 999999}), 404, "NOT_FOUND")
        assert_error(c.post("/api/sessions/blender-9/import", json={"asset_id": got["m.obj"]["id"]}),
                     404, "NOT_FOUND")
        r = c.post("/api/sessions/blender-5/import", json={"asset_id": got["m.obj"]["id"]})
        assert_error(r, 502, "BRIDGE_FAILED")
        assert "nope" in r.json()["error"]["message"]
        assert_error(c.post("/api/sessions/blender-4242/import", json={}), 422, "VALIDATION_ERROR")
        (lib / "m.obj").unlink()
        assert_error(c.post("/api/sessions/blender-4242/import", json={"asset_id": got["m.obj"]["id"]}),
                     404, "NOT_FOUND")
        drain(app, c)


def test_bridges_api(daemon, tmp_path, monkeypatch):
    for ver in ("4.2", "5.0"):
        write(tmp_path / "PF" / "Blender Foundation" / f"Blender {ver}" / "blender.exe", b"")
    platform = sys.platform if sys.platform in dcc.CANDIDATES else "linux"
    monkeypatch.setitem(dcc.CANDIDATES, platform, [str(tmp_path / "PF" / "Blender Foundation" / "Blender *" /
                                                       "blender.exe")])
    monkeypatch.setenv("BLENDER_USER_RESOURCES", str(tmp_path / "profile"))
    installs = []

    def fake_install(exe):
        installs.append(exe)
        if "4.2" not in exe:
            raise bridge.BridgeError("Error: Add-on not loaded")
        write(bridge.addon_path("4.2"), b"addon")

    monkeypatch.setattr(bridge, "install", fake_install)
    with daemon() as (_app, c):
        r = c.get("/api/bridges")
        assert_shape(r.json(), {"type": "array", "items": {"$ref": "Bridge"}})
        assert [(b["app_id"], b["installed"]) for b in r.json()] == [("blender-4.2", False), ("blender-5.0", False)]
        r = c.post("/api/bridges/blender-4.2")
        assert r.status_code == 204 and r.content == b""
        assert [b["installed"] for b in c.get("/api/bridges").json()] == [True, True]
        assert_error(c.post("/api/bridges/blender-5.0"), 502, "BRIDGE_FAILED")
        for bad in ("explorer", "open-with", "blender-9.9", "maya-2025"):
            assert_error(c.post(f"/api/bridges/{bad}"), 404, "NOT_FOUND")
    assert [Path(e).parent.name for e in installs] == ["Blender 4.2", "Blender 5.0"]


def test_launch_commands():
    native = str(Path("C:/My Lib/a b.obj"))
    blender = {"id": "blender-5.2", "executable": "C:/B/blender.exe"}
    assert dcc.launch_command(blender, "C:/My Lib/a b.obj") == [
        "C:/B/blender.exe", "--python", str(dcc.OPEN_IMPORT_SCRIPT), "--", native]
    assert dcc.launch_command(blender, "C:/My Lib/x.BLEND") == ["C:/B/blender.exe", str(Path("C:/My Lib/x.BLEND"))]
    assert dcc.launch_command({"id": "maya-2025", "executable": "C:/M/maya.exe"}, "C:/r.ma") == [
        "C:/M/maya.exe", str(Path("C:/r.ma"))]
    explorer = dcc.launch_command({"id": "explorer", "executable": "C:/WINDOWS/explorer.exe"}, "C:/My Lib/a b.obj")
    assert explorer == f'"{Path("C:/WINDOWS/explorer.exe")}" /select,"{native}"'
    picker = dcc.launch_command({"id": "open-with", "executable": "C:/W/System32/rundll32.exe"}, "C:/My Lib/a b.obj")
    assert picker == f'"{Path("C:/W/System32/rundll32.exe")}" shell32.dll,OpenAs_RunDLL {native}'


needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender not installed")


@pytest.mark.blender
@needs_blender
@pytest.mark.parametrize("name", ["suzanne.obj", "suzanne.fbx", "nopreview.blend"])
def test_open_import_script_leaves_only_the_imported_mesh(real_fixtures, name):
    out = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
         str(ROOT / "scripts" / "blender_open_import.py"), "--python-expr",
         "import bpy;print('TYPES', sorted(o.type for o in bpy.data.objects))", "--", str(real_fixtures / name)],
        capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "TYPES ['MESH']" in out.stdout


@pytest.mark.blender
@needs_blender
def test_install_writes_only_into_the_isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDER_USER_RESOURCES", str(tmp_path / "profile"))
    real = Path(os.environ.get("APPDATA", Path.home())) / "Blender Foundation" / "Blender"
    before = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in real.glob("*/config/userpref.blend")}
    bridge.install(BLENDER)
    assert bridge.addon_path("any").read_bytes() == (ROOT / "scripts" / "dam_bridge.py").read_bytes()
    assert (tmp_path / "profile" / "config" / "userpref.blend").is_file()
    assert {p: hashlib.md5(p.read_bytes()).hexdigest() for p in real.glob("*/config/userpref.blend")} == before

