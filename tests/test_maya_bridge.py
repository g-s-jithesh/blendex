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
from conftest import MAYA, MAYA_ENV, ROOT, add_library, assert_error, assert_shape, by_name, drain

from backend import bridge, dcc

TOKEN = "m" * 32
MAYA_EXTS = ["ma", "mb", "obj", "fbx", "dae", "usd", "usda", "usdc", "usdz", "abc", "stl"]


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    folder = tmp_path / "sessions"
    folder.mkdir(exist_ok=True)
    monkeypatch.setenv("DAM_SESSIONS", str(folder))
    servers = []

    def start(app="maya", pid=4242, reply=None, name=None):
        seen = []
        version = "2027" if app == "maya" else "5.2"

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline())
                seen.append(req)
                if req.get("token") != TOKEN:
                    ans = {"ok": False, "error": "bad token"}
                elif req.get("cmd") == "info":
                    ans = {"ok": True, "pid": pid, "version": version, "file": None}
                else:
                    ans = reply or {"ok": True, "imported": ["pCube1", "pSphere1"]}
                self.wfile.write(json.dumps(ans).encode() + b"\n")

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        (folder / f"{name or f'{app}-{pid}'}.json").write_text(json.dumps(
            {"app": app, "pid": pid, "port": server.server_address[1], "token": TOKEN}))
        return seen

    yield folder, start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_sessions_lists_maya_next_to_blender_and_drops_stale_maya(daemon, sessions):
    folder, start = sessions
    start()
    start(app="blender", pid=77)
    (folder / "maya-1.json").write_text(json.dumps({"app": "maya", "pid": 1, "port": closed_port(), "token": TOKEN}))
    (folder / "maya-2.json").write_text("{")
    with daemon() as (_app, c):
        r = c.get("/api/sessions")
    assert r.status_code == 200
    assert_shape(r.json(), {"type": "array", "items": {"$ref": "Session"}})
    assert r.json() == [
        {"id": "blender-77", "app": "Blender", "version": "5.2", "pid": 77, "file": None,
         "exts": ["blend", *dcc.BLENDER_IMPORTS]},
        {"id": "maya-4242", "app": "Maya", "version": "2027", "pid": 4242, "file": None, "exts": MAYA_EXTS},
    ]
    assert sorted(p.name for p in folder.iterdir()) == ["blender-77.json", "maya-4242.json"]


def test_maya_import_api(daemon, lib, sessions, monkeypatch, tmp_path):
    folder, start = sessions
    seen = start()
    start(pid=5, reply={"ok": False, "error": "RuntimeError: Maya is busy"})
    malformed = ["maya-1x", "..%2Fx", "blender-", "mayax-1", "maya-4242%2F..%2Fmaya-4242"]
    for name in ("maya-1x", "blender-", "mayax-1"):
        start(pid=9, name=name)
    write(tmp_path / "x.json", json.dumps({"port": closed_port(), "token": TOKEN}).encode())
    reads = []
    real_read = bridge._read
    monkeypatch.setattr(bridge, "_read", lambda p: reads.append(Path(p)) or real_read(p))
    for name in ("m.obj", "c.mb", "h.blend", "s.stl"):
        write(lib / name)
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = {k: v["id"] for k, v in by_name(c).items()}

        def imp(sid, name):
            return c.post(f"/api/sessions/{sid}/import", json={"asset_id": got[name]})

        for name in ("m.obj", "c.mb", "s.stl"):
            r = imp("maya-4242", name)
            assert r.status_code == 200 and r.json() == {"imported": ["pCube1", "pSphere1"]}, name
            assert seen[-1] == {"cmd": "import", "path": str(lib / name), "token": TOKEN}
        r = imp("maya-4242", "h.blend")
        assert_error(r, 409, "UNSUPPORTED")
        assert "Maya" in r.json()["error"]["message"]
        assert_error(imp("maya-99999", "m.obj"), 404, "NOT_FOUND")
        before = len(seen)
        for sid in malformed:
            assert_error(imp(sid, "m.obj"), 404, "NOT_FOUND")
        assert len(seen) == before
        r = imp("maya-5", "m.obj")
        assert_error(r, 502, "BRIDGE_FAILED")
        assert "Maya is busy" in r.json()["error"]["message"]
        drain(app, c)
    assert reads and all(p.parent == folder for p in reads)
    assert {p.stem for p in reads} <= {"maya-4242", "maya-99999", "maya-5"}


def test_import_into_rejects_malformed_ids_before_any_lookup(sessions, monkeypatch):
    _folder, start = sessions
    start()
    monkeypatch.setattr(bridge, "_read", lambda p: pytest.fail(f"read {p}"))
    for bad in ("maya-1x", "../x", "blender-", "mayax-1", "maya-", "maya-4242/../maya-4242", "MAYA-4242", " maya-1"):
        with pytest.raises(LookupError):
            bridge.import_into(bad, "C:/lib/a.obj")


def test_maya_plugin_path_honours_maya_app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAYA_APP_DIR", str(tmp_path / "prefs"))
    assert bridge.maya_plugin_path("2027") == tmp_path / "prefs" / "2027" / "plug-ins" / "maya_bridge.py"


@pytest.fixture
def fake_maya(tmp_path, monkeypatch):
    exe = write(tmp_path / "PF" / "Autodesk" / "Maya2027" / "bin" / "maya.exe", b"")
    platform = sys.platform if sys.platform in dcc.CANDIDATES else "linux"
    monkeypatch.setitem(dcc.CANDIDATES, platform, [str(tmp_path / "PF" / "Autodesk" / "Maya*" / "bin" / "maya.exe")])
    monkeypatch.setenv("MAYA_APP_DIR", str(tmp_path / "maya_prefs"))
    runs = []

    def run(result):
        def fake_run(argv, **kw):
            runs.append((argv, kw))
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(argv, *result)
        monkeypatch.setattr(bridge, "subprocess", SimpleNamespace(run=fake_run, DEVNULL=subprocess.DEVNULL))

    return exe.resolve(), runs, run


def test_bridges_lists_and_installs_maya(daemon, fake_maya, tmp_path):
    exe, runs, run = fake_maya
    with daemon() as (_app, c):
        r = c.get("/api/bridges")
        assert_shape(r.json(), {"type": "array", "items": {"$ref": "Bridge"}})
        assert r.json() == [{"app_id": "maya-2027", "name": "Maya 2027", "installed": False}]
        write(tmp_path / "maya_prefs" / "2027" / "plug-ins" / "maya_bridge.py")
        assert c.get("/api/bridges").json()[0]["installed"] is False
        write(tmp_path / "maya_prefs" / "2027" / "scripts" / "maya_safety.py")
        assert c.get("/api/bridges").json()[0]["installed"] is True

        run((0, "C:/x/plug-ins/maya_bridge.py\n", ""))
        r = c.post("/api/bridges/maya-2027")
        assert r.status_code == 204 and r.content == b""
        argv, kw = runs[-1]
        mayapy = "mayapy.exe" if sys.platform == "win32" else "mayapy"
        assert argv == [str(exe.with_name(mayapy)), str(ROOT / "scripts" / "install_maya_bridge.py")]
        assert kw["env"]["MAYA_SKIP_USERSETUP_PY"] == "1" and kw["env"]["MAYA_APP_DIR"] == str(tmp_path / "maya_prefs")
        assert kw["timeout"] > 0 and kw["stdin"] == subprocess.DEVNULL

        error = "Error: start Maya 2027 once so it creates its preferences, then install again"
        run((1, "", f"Traceback (most recent call last):\nSystemExit\n{error}\n"))
        r = c.post("/api/bridges/maya-2027")
        assert_error(r, 502, "BRIDGE_FAILED")
        assert error in r.json()["error"]["message"] and "Maya 2027" in r.json()["error"]["message"]
        run((3, "", "boom"))
        assert "Maya exited with code 3" in c.post("/api/bridges/maya-2027").json()["error"]["message"]
        run(subprocess.TimeoutExpired("mayapy", 180))
        assert_error(c.post("/api/bridges/maya-2027"), 502, "BRIDGE_FAILED")
        for bad in ("maya-2026", "maya", "maya-2027x"):
            assert_error(c.post(f"/api/bridges/{bad}"), 404, "NOT_FOUND")
    assert len(runs) == 4


needs_maya = pytest.mark.skipif(MAYA is None, reason="no Maya with mayapy installed")


def mayapy_env(tmp_path) -> dict[str, str]:
    return os.environ | MAYA_ENV | {"MAYA_APP_DIR": str(tmp_path / "maya_app"), "DAM_SESSIONS": str(tmp_path / "s")}


@pytest.mark.maya
@needs_maya
def test_real_installer_refuses_a_never_started_maya(tmp_path, monkeypatch):
    env = mayapy_env(tmp_path)
    monkeypatch.setenv("MAYA_APP_DIR", env["MAYA_APP_DIR"])
    with pytest.raises(bridge.BridgeError, match=rf"^Error: start Maya {MAYA['version']}\S* once .*install again$"):
        bridge.install(MAYA["executable"])
    assert not [p for p in (tmp_path / "maya_app").rglob("*") if p.name.startswith(("maya_bridge", "pluginPrefs"))]


@pytest.mark.maya
@needs_maya
def test_real_batch_maya_loads_the_plugin_without_a_session(tmp_path):
    env = mayapy_env(tmp_path)
    plugin = (ROOT / "scripts" / "maya_bridge.py").as_posix()
    code = ("import maya.standalone as s; s.initialize(name='python'); from maya import cmds; "
            f"cmds.loadPlugin(r'{plugin}'); print('LOADED', cmds.pluginInfo('maya_bridge', q=True, loaded=True)); "
            "cmds.unloadPlugin('maya_bridge'); s.uninitialize()")
    out = subprocess.run([str(bridge.mayapy(MAYA["executable"])), "-c", code], env=env, capture_output=True,
                         text=True, timeout=300, check=False)
    assert "LOADED True" in out.stdout, (out.returncode, out.stdout[-2000:], out.stderr[-2000:])
    assert not (tmp_path / "s").exists() or not list((tmp_path / "s").iterdir())
