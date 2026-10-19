import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import (
    BLENDER,
    MAYA,
    MAYA_ENV,
    PNG_SIG,
    add_library,
    by_name,
    decode_png,
    drain,
    fake_procs,
    items,
    no_fake_procs,
    settle,
    spawned,
    wait_until,
)

from backend import bridge, dcc

FAKE_MAYAPY = Path(__file__).resolve().parent / "fake_mayapy.py"
TMP_FBX = re.compile(r"\d+\.[0-9a-f]{8}\.tmp\.fbx")


@pytest.fixture
def maya_log(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("DAM_MAYAPY", str(FAKE_MAYAPY))
    monkeypatch.setenv("FAKE_MAYA_APP_DIR", str(tmp_path / "maya"))
    monkeypatch.setenv("FAKE_MAYA_LOG", str(tmp_path / "maya_spawns"))
    for k in ("FAKE_MAYA_SLEEP", "FAKE_MAYA_FAIL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "maya_spawns"


def maya_runs(log: Path) -> list[tuple[str, int, int | None]]:
    runs = []
    for f in log.glob("*.txt") if log.exists() else []:
        src, start = f.read_text(encoding="utf-8").split("\t")
        end = f.with_suffix(".end")
        runs.append((Path(src).name, int(start), int(end.read_text()) if end.exists() else None))
    return sorted(runs, key=lambda r: r[1])


def cache_names(lib: Path) -> list[str]:
    return sorted(p.name for p in (lib / ".cache").iterdir()) if (lib / ".cache").exists() else []


def status(c, asset_id: int) -> str:
    return c.get(f"/api/assets/{asset_id}").json()["status"]


def test_mb_and_ma_chain_mayapy_into_blender(daemon, lib, maya_log, spawn_log, tmp_path):
    (lib / "cube.mb").write_bytes(b"FOR8 not really")
    (lib / "rig.ma").write_bytes(b'//Maya ASCII 2027 scene\nrequires maya "2027";\n')
    (lib / "m.obj").write_bytes(b"o")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        for name in ("cube.mb", "rig.ma", "m.obj"):
            a = got[name]
            assert (a["status"], a["error"], a["has_thumbnail"], a["has_proxy"], a["poly_count"]) == (
                "ready", None, True, True, 1234), name
            assert c.get(f"/api/assets/{a['id']}/proxy").content[:4] == b"glTF"
            assert decode_png(c.get(f"/api/assets/{a['id']}/thumbnail").content)[:2] == (1, 1)
        assert got["rig.ma"]["dcc_version"] == "2027"
    assert sorted(r[0] for r in maya_runs(maya_log)) == ["cube.mb", "rig.ma"]
    assert all(r[2] for r in maya_runs(maya_log))
    blender_inputs = sorted(name for name, mode in spawned(spawn_log) if mode == "full")
    assert len(blender_inputs) == 3 and blender_inputs[2] == "m.obj"
    assert all(TMP_FBX.fullmatch(n) for n in blender_inputs[:2]), blender_inputs
    assert (tmp_path / "maya").is_dir()
    ids = sorted(a["id"] for a in got.values())
    assert cache_names(lib) == sorted(f"{i}.{e}" for i in ids for e in ("glb", "png"))


def test_maya_failure_fails_the_asset_and_cleans_temp_files(daemon, lib, maya_log, spawn_log, monkeypatch):
    monkeypatch.setenv("FAKE_MAYA_FAIL", "1")
    (lib / "cube.mb").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = by_name(c)["cube.mb"]
        assert (a["status"], a["error"], a["has_thumbnail"], a["has_proxy"]) == (
            "failed", "RuntimeError: scene has no visible meshes", False, False)
        assert len(maya_runs(maya_log)) == 1 and spawned(spawn_log) == []
        assert cache_names(lib) == []

        monkeypatch.delenv("FAKE_MAYA_FAIL")
        assert c.post(f"/api/assets/{a['id']}/convert").status_code == 202
        drain(app, c)
        assert status(c, a["id"]) == "ready" and len(maya_runs(maya_log)) == 2


def test_no_maya_fails_maya_assets_only(daemon, lib, maya_log, spawn_log, monkeypatch, tmp_path):
    monkeypatch.delenv("DAM_MAYAPY")
    (tmp_path / "PF" / "Autodesk" / "Maya2027" / "bin").mkdir(parents=True)
    (tmp_path / "PF" / "Autodesk" / "Maya2027" / "bin" / "maya.exe").write_bytes(b"")
    monkeypatch.setitem(dcc.CANDIDATES, sys.platform if sys.platform in dcc.CANDIDATES else "linux",
                        [str(tmp_path / "PF" / "Autodesk" / "Maya*" / "bin" / "maya.exe")])
    (lib / "cube.mb").write_bytes(b"x")
    (lib / "rig.ma").write_bytes(b"x")
    (lib / "m.obj").write_bytes(b"o")
    with daemon() as (app, c):
        assert [a["id"] for a in c.get("/api/apps").json() if a["id"].startswith("maya")] == ["maya-2027"]
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    for name in ("cube.mb", "rig.ma"):
        assert (got[name]["status"], got[name]["error"]) == ("failed", "Maya not found; install Maya or set DAM_MAYAPY")
    assert got["m.obj"]["status"] == "ready"
    assert maya_runs(maya_log) == [] and [n for n, _ in spawned(spawn_log)] == ["m.obj"]


def test_startup_repends_unsupported_maya_rows(daemon, lib, maya_log, monkeypatch, tmp_path):
    monkeypatch.delenv("DAM_CONVERT")
    (lib / "cube.mb").write_bytes(b"x")
    (lib / "s.stl").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
    with sqlite3.connect(tmp_path / "t.sqlite") as conn:
        conn.execute("UPDATE versions SET status = 'unsupported', error = 'old rule' WHERE id ="
                     " (SELECT current_version_id FROM assets WHERE ext = 'mb')")
    conn.close()
    with daemon() as (app, c):
        drain(app, c)
        got = by_name(c)
        assert {k: (v["status"], v["error"]) for k, v in got.items()} == {
            "cube.mb": ("pending", None), "s.stl": ("unsupported", None)}
        assert maya_runs(maya_log) == []
        assert c.post(f"/api/assets/{got['cube.mb']['id']}/convert").status_code == 202
        drain(app, c)
        assert status(c, got["cube.mb"]["id"]) == "ready"


def queue(c) -> dict:
    return c.get("/api/queue").json()


def test_pause_and_dedupe_cover_the_maya_step(daemon, lib, maya_log, spawn_log, monkeypatch):
    monkeypatch.delenv("DAM_CONVERT")
    monkeypatch.setenv("FAKE_MAYA_SLEEP", "2")
    (lib / "cube.mb").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        aid = by_name(c)["cube.mb"]["id"]
        c.post("/api/queue/pause")
        for _ in range(3):
            assert c.post(f"/api/assets/{aid}/convert").status_code == 202
        time.sleep(1)
        assert queue(c) == {"queued": 1, "running": 0, "paused": True} and maya_runs(maya_log) == []
        c.post("/api/queue/resume")
        wait_until(lambda: maya_runs(maya_log))
        for _ in range(3):
            assert c.post(f"/api/assets/{aid}/convert").status_code == 202
        assert queue(c) == {"queued": 0, "running": 1, "paused": False} and status(c, aid) == "converting"
        drain(app, c)
        assert status(c, aid) == "ready"
    assert len(maya_runs(maya_log)) == 1 and len(spawned(spawn_log)) == 1


def test_first_maya_export_runs_alone_then_in_parallel(daemon, lib, maya_log, monkeypatch):
    monkeypatch.setenv("FAKE_MAYA_SLEEP", "1.5")
    monkeypatch.setenv("DAM_WORKERS", "3")
    for i in range(2):
        (lib / f"cold{i}.mb").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        first = maya_runs(maya_log)
        assert len(first) == 2 and first[1][1] >= first[0][2], first
        for i in range(2):
            (lib / f"warm{i}.mb").write_bytes(b"x")
        settle(app, c)
        drain(app, c)
        assert {a["status"] for a in items(c)} == {"ready"}
    warm = maya_runs(maya_log)[2:]
    assert sorted(r[0] for r in warm) == ["warm0.mb", "warm1.mb"]
    assert warm[1][1] < warm[0][2], warm


def test_shutdown_kills_a_running_maya_step(daemon, lib, maya_log, spawn_log, monkeypatch):
    monkeypatch.setenv("FAKE_MAYA_SLEEP", "120")
    (lib / "cube.mb").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        wait_until(lambda: maya_runs(maya_log), timeout=30)
        assert fake_procs(needle="fake_mayapy")
        t = time.monotonic()
    assert time.monotonic() - t < 30
    assert no_fake_procs(needle="fake_mayapy") and not app.state.worker.procs
    assert maya_runs(maya_log)[0][2] is None and cache_names(lib) == [] and spawned(spawn_log) == []

    monkeypatch.setenv("FAKE_MAYA_SLEEP", "0")
    with daemon() as (app, c):
        drain(app, c)
        assert {a["status"] for a in items(c)} == {"ready"}
    assert len(maya_runs(maya_log)) == 2


def test_maya_job_timeout_kills_mayapy(daemon, lib, maya_log, spawn_log, monkeypatch):
    monkeypatch.setenv("FAKE_MAYA_SLEEP", "120")
    monkeypatch.setenv("DAM_JOB_TIMEOUT", "2")
    (lib / "cube.mb").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = by_name(c)["cube.mb"]
        assert (a["status"], a["error"]) == ("failed", "timed out after 2s")
    assert no_fake_procs(needle="fake_mayapy") and cache_names(lib) == [] and spawned(spawn_log) == []


@pytest.mark.maya
@pytest.mark.blender
@pytest.mark.skipif(MAYA is None or BLENDER is None, reason="needs both Maya (mayapy) and Blender")
def test_real_mb_reaches_ready_through_mayapy_and_blender(daemon, lib, monkeypatch, tmp_path):
    mayapy = bridge.mayapy(MAYA["executable"])
    code = ("import maya.standalone as s; s.initialize(name='python'); import maya.cmds as c; c.polyCube(); "
            f"c.file(rename=r'{(lib / 'cube.mb').as_posix()}'); c.file(save=True, type='mayaBinary'); "
            "s.uninitialize()")
    env = os.environ | MAYA_ENV | {"MAYA_APP_DIR": str(tmp_path / "gen_maya")}
    subprocess.run([str(mayapy), "-c", code], env=env, check=True, timeout=300, capture_output=True)
    monkeypatch.setenv("DAM_BLENDER", BLENDER)
    monkeypatch.setenv("DAM_MAYAPY", str(mayapy))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = by_name(c)["cube.mb"]
        assert (a["status"], a["error"], a["has_thumbnail"], a["has_proxy"], a["poly_count"]) == (
            "ready", None, True, True, 12), a
        assert a["dcc_version"] == MAYA["version"]
        assert c.get(f"/api/assets/{a['id']}/proxy").content[:4] == b"glTF"
        png = c.get(f"/api/assets/{a['id']}/thumbnail").content
        assert png[:8] == PNG_SIG and png[12:16] == b"IHDR" and struct.unpack(">II", png[16:24]) == (512, 512)
    assert (tmp_path / "maya").is_dir()
    assert cache_names(lib) == [f"{a['id']}.glb", f"{a['id']}.png"]
