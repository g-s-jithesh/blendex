import hashlib
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import BLENDER, ROOT, add_library, assert_error, assert_shape, by_name, drain

from backend import bridge, dcc
from backend.indexer import EXTS

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


def detect(monkeypatch, platform, candidates=(), tools=None, run=None):
    monkeypatch.setattr(dcc, "sys", SimpleNamespace(platform=platform, executable=sys.executable))
    monkeypatch.setitem(dcc.CANDIDATES, platform, [str(c) for c in candidates])
    monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which=(tools or {}).get))
    calls = []

    def fake_run(args, **kw):
        calls.append((args, kw))
        if isinstance(run, BaseException):
            raise run
        return SimpleNamespace(stdout=run or "")

    monkeypatch.setattr(dcc, "subprocess", SimpleNamespace(run=fake_run, DEVNULL=subprocess.DEVNULL,
                                                           SubprocessError=subprocess.SubprocessError))
    return dcc.detect_apps(), calls


@pytest.mark.parametrize("tools, ids", [
    ({"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"}, ["file-manager", "default-app"]),
    ({"dbus-send": "/usr/bin/dbus-send"}, ["file-manager"]),
    ({"xdg-open": "/usr/bin/xdg-open"}, ["file-manager", "default-app"]),
    ({}, []),
])
def test_linux_lists_file_manager_and_default_app(monkeypatch, tools, ids):
    apps, _ = detect(monkeypatch, "linux", tools=tools)
    assert [a["id"] for a in apps] == ids
    exes = {"file-manager": tools.get("dbus-send") or tools.get("xdg-open"), "default-app": tools.get("xdg-open")}
    assert apps == [{"id": i, "name": i.replace("-", " "), "version": None, "executable": exes[i],
                     "exts": sorted(EXTS)} for i in ids]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_xdg_openers_are_linux_only(monkeypatch, platform):
    apps, _ = detect(monkeypatch, platform, tools={"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"})
    assert [a["id"] for a in apps] == (["explorer", "open-with"] if platform == "win32" else [])


def test_linux_launch_commands(monkeypatch):
    path, uri = (("C:/My Lib/ä,b c.obj", "file:///C:/My%20Lib/%C3%A4%2Cb%20c.obj") if os.name == "nt" else
                 ("/home/u/My Lib/ä,b c.obj", "file:///home/u/My%20Lib/%C3%A4%2Cb%20c.obj"))
    tools = {"dbus-send": "/usr/bin/dbus-send", "xdg-open": "/usr/bin/xdg-open"}
    monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which=tools.get))
    files = {"id": "file-manager", "executable": "/usr/bin/dbus-send"}
    assert dcc.launch_command(files, path) == [
        "/usr/bin/dbus-send", "--session", "--print-reply", "--reply-timeout=5000",
        "--dest=org.freedesktop.FileManager1",
        "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:"]
    assert dcc.launch_command({"id": "default-app", "executable": "/usr/bin/xdg-open"}, path) == [
        "/usr/bin/xdg-open", str(Path(path))]
    del tools["dbus-send"]
    assert dcc.launch_command(files, path) == ["/usr/bin/xdg-open", str(Path(path).parent)]



@pytest.mark.parametrize(("rc", "fallback"), [(0, []), (1, [["/usr/bin/xdg-open", "/lib"]])])
def test_show_in_folder_falls_back_when_no_file_manager_answers(monkeypatch, rc, fallback):
    monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which={"xdg-open": "/usr/bin/xdg-open"}.get))
    monkeypatch.setattr(dcc, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": lambda *a, **k: SimpleNamespace(returncode=rc)}))
    spawned = []
    monkeypatch.setattr(dcc, "_spawn", spawned.append)
    dcc._show_items(["dbus-send", "--print-reply"], "/lib")
    assert spawned == fallback

@pytest.mark.parametrize("out, version", [
    ("Blender 4.2.3 LTS\n\tbuild date: 2024-10-15\n", "4.2"),
    ("Blender 5.2.2 LTS (hash abc built 2026-01-01)\n", "5.2"),
    ("snap-confine has elevated permissions\n", None),
    ("", None),
    (subprocess.TimeoutExpired(["blender"], 10), None),
    (OSError(8, "Exec format error"), None),
])
def test_linux_reads_blender_version_from_cli(monkeypatch, tmp_path, out, version):
    exe = write(tmp_path / "usr" / "bin" / "blender", b"")
    apps, calls = detect(monkeypatch, "linux", [exe], tools={"blender": str(exe)}, run=out)
    assert [(a["id"], a["version"]) for a in apps] == [(f"blender-{version}" if version else "blender", version)]
    assert calls == [([str(exe.resolve()), "--version"], {"check": False, "stdin": subprocess.DEVNULL,
                      "capture_output": True, "text": True, "errors": "replace", "timeout": 10})]


@pytest.mark.parametrize("platform, name", [("linux", "blender-4.5.1-linux-x64"), ("win32", "Blender")])
def test_cli_version_only_when_the_path_has_none_and_not_on_windows(monkeypatch, tmp_path, platform, name):
    exe = write(tmp_path / name / "blender", b"")
    apps, calls = detect(monkeypatch, platform, [exe], run="Blender 9.9.0\n")
    assert calls == []
    assert [a["id"] for a in apps if a["id"].startswith("blender")] == ["blender-4.5" if "4.5" in name else "blender"]


def test_snap_symlink_keeps_its_own_path(monkeypatch, tmp_path):
    snap = write(tmp_path / "usr" / "bin" / "snap", b"")
    link = tmp_path / "snap" / "bin" / "blender"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(snap)
    except OSError:
        pytest.skip("symlinks need privileges here")
    apps, calls = detect(monkeypatch, "linux", [link], run="Blender 4.2.3 LTS\n")
    assert [(a["id"], a["executable"]) for a in apps] == [("blender-4.2", link.absolute().as_posix())]
    assert calls[0][0] == [str(link.absolute()), "--version"]


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
    monkeypatch.delenv("BLENDER_USER_RESOURCES", raising=False)
    real = bridge.addon_path("any").parents[3]
    monkeypatch.setenv("BLENDER_USER_RESOURCES", str(tmp_path / "profile"))
    before = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in real.glob("*/config/userpref.blend")}
    bridge.install(BLENDER)
    assert bridge.addon_path("any").read_bytes() == (ROOT / "scripts" / "dam_bridge.py").read_bytes()
    assert (tmp_path / "profile" / "config" / "userpref.blend").is_file()
    assert {p: hashlib.md5(p.read_bytes()).hexdigest() for p in real.glob("*/config/userpref.blend")} == before

