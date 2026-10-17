import time

from conftest import (
    add_library,
    blend,
    by_name,
    drain,
    fake_procs,
    items,
    no_fake_procs,
    preview_blend,
    spawned,
    spawns,
    wait_until,
)


def temp_files(lib) -> list[str]:
    cache = lib / ".cache"
    return [p.name for p in cache.iterdir() if p.suffix not in (".glb", ".png") or ".tmp" in p.name] \
        if cache.exists() else []


def fill(lib, n: int) -> None:
    for i in range(n):
        (lib / f"a{i:02}.blend").write_bytes(blend("4.2"))


def test_shutdown_mid_flight_kills_every_process(daemon, lib, monkeypatch, spawn_log):
    no_fake_procs()
    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "120")
    monkeypatch.setenv("DAM_WORKERS", "8")
    fill(lib, 20)
    with daemon() as (app, c):
        add_library(c, lib)
        wait_until(lambda: len(app.state.worker.procs) == 8 and spawns(spawn_log) == 8, timeout=60)
        assert app.state.worker.running == 8 and len(app.state.worker.queued) == 12
        assert len(fake_procs()) >= 8
        assert {a["status"] for a in items(c)} == {"converting", "pending"}
        t = time.monotonic()
    assert time.monotonic() - t < 30
    assert no_fake_procs()
    assert not app.state.worker.procs
    assert spawns(spawn_log) == 8
    assert temp_files(lib) == []

    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "0")
    with daemon() as (app, c):
        drain(app, c)
        assert {a["status"] for a in items(c)} == {"ready"}
    assert spawns(spawn_log) == 28 and {mode for _, mode in spawned(spawn_log)} == {"thumb"}
    assert no_fake_procs() and temp_files(lib) == []


def test_job_timeout_kills_process(daemon, lib, monkeypatch, spawn_log):
    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "120")
    monkeypatch.setenv("DAM_JOB_TIMEOUT", "1")
    fill(lib, 3)
    with daemon() as (app, c):
        add_library(c, lib)
        t = time.monotonic()
        drain(app, c)
        assert time.monotonic() - t < 30
        assert no_fake_procs()
        got = items(c)
        assert spawns(spawn_log) == 3
        assert {(a["status"], a["error"], a["has_proxy"]) for a in got} == {("failed", "timed out after 1s", False)}
        assert temp_files(lib) == []


def test_reads_never_spawn_conversions(daemon, lib, monkeypatch, spawn_log):
    (lib / "a.blend").write_bytes(blend("4.2"))
    (lib / "p.blend").write_bytes(preview_blend())
    (lib / "m.obj").write_bytes(b"o")
    with daemon() as (app, c):
        lib_id = add_library(c, lib)["id"]
        drain(app, c)
        got = by_name(c)
        aid, pid, oid = got["a.blend"]["id"], got["p.blend"]["id"], got["m.obj"]["id"]
        assert sorted(spawned(spawn_log)) == [("a.blend", "thumb"), ("m.obj", "full")]
        gets = [r.path for r in app.routes if "GET" in getattr(r, "methods", ()) and r.path.startswith("/api")]
        for _ in range(10):
            for path in gets:
                for i in (aid, pid, oid, 999999):
                    url = path.replace("{asset_id}", str(i)).replace("{library_id}", str(lib_id))
                    c.get(url)
                    c.get(url, params={"v": "123"})
            with c.websocket_connect("/ws/events"):
                pass
        drain(app, c)
        assert spawns(spawn_log) == 2

        for f in (lib / ".cache").iterdir():
            f.unlink()
        for _ in range(10):
            for i in (aid, pid):
                assert c.get(f"/api/assets/{i}/proxy").status_code == 404
            for i in (aid, pid, oid):
                assert c.get(f"/api/assets/{i}/thumbnail").status_code in (200, 404)
            assert c.get(f"/api/assets/{oid}/proxy").status_code in (200, 404)
        drain(app, c)
        assert sorted(spawned(spawn_log)[2:]) == [("a.blend", "thumb"), ("m.obj", "full")]
        assert {a["status"] for a in items(c)} == {"ready"}
        assert c.get(f"/api/assets/{oid}/proxy").status_code == 200
        for i in (aid, pid, oid):
            assert c.get(f"/api/assets/{i}/thumbnail").status_code == 200

        monkeypatch.setenv("FAKE_BLENDER_SLEEP", "3")
        for f in (lib / ".cache").iterdir():
            f.unlink()
        for _ in range(20):
            assert c.get(f"/api/assets/{oid}/proxy").status_code == 404
            time.sleep(0.05)
        assert c.get(f"/api/assets/{oid}").json()["status"] in ("pending", "converting")
        drain(app, c)
        assert spawned(spawn_log)[4:] == [("m.obj", "full")]
        assert c.get(f"/api/assets/{oid}/proxy").status_code == 200
    assert no_fake_procs()


def test_shutdown_while_processes_are_starting(daemon, lib, monkeypatch, spawn_log):
    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "120")
    monkeypatch.setenv("DAM_WORKERS", "8")
    fill(lib, 20)
    with daemon() as (app, c):
        add_library(c, lib)
    for delay in (0.05, 0.2, 0.5):
        with daemon() as (app, c):
            wait_until(lambda: app.state.worker.running, step=0.01)
            time.sleep(delay)
        assert not app.state.worker.procs
        assert no_fake_procs(timeout=15)
        assert temp_files(lib) == []
