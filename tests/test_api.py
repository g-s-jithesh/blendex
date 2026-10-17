import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys

import pytest
from compression import zstd
from conftest import (
    EXPECTED_2X2,
    PNG_SIG,
    SPEC,
    add_library,
    assert_error,
    assert_shape,
    blend,
    by_name,
    decode_png,
    drain,
    items,
    preview_blend,
    spawned,
    spawns,
    wait_until,
    ws_until,
)

from backend import dcc

DOC_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def write(path, data: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def blender_install(tmp_path, monkeypatch):
    exes = []
    for ver in ("4.2", "5.0"):
        exe = write(tmp_path / "PF" / "Blender Foundation" / f"Blender {ver}" / "blender.exe", b"")
        exes.append(exe.resolve().as_posix())
    platform = sys.platform if sys.platform in dcc.CANDIDATES else "linux"
    monkeypatch.setitem(dcc.CANDIDATES, platform, [str(tmp_path / "PF" / "Blender Foundation" / "Blender *" /
                                                       "blender.exe")])
    return exes


@pytest.fixture
def popen(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(dcc.subprocess, "Popen", fake)
    return calls


def test_every_spec_endpoint_is_routed_and_nothing_else(daemon):
    spec = {(m.upper(), "/api" + p.replace("{id}", "{x}")) for p, ops in SPEC["paths"].items()
            for m in ops if m != "parameters"}
    with daemon() as (app, _c):
        routes = set()
        for r in app.routes:
            if r.path in DOC_ROUTES or r.path == "/ws/events":
                continue
            path = r.path.replace("{library_id}", "{x}").replace("{asset_id}", "{x}")
            routes |= {(m, path) for m in r.methods - {"HEAD"}}
    assert routes == spec


def test_health(daemon):
    with daemon() as (_app, c):
        r = c.get("/api/health")
    assert r.status_code == 200
    assert_shape(r.json(), "Health")


def test_libraries_crud_and_errors(daemon, lib, tmp_path):
    (tmp_path / "file.txt").write_text("x")
    other = tmp_path / "other"
    other.mkdir()
    with daemon() as (app, c):
        assert c.get("/api/libraries").json() == []
        r = c.post("/api/libraries", json={"path": str(lib) + os.sep})
        assert r.status_code == 201
        created = r.json()
        assert_shape(created, "Library")
        assert created["path"] == lib.as_posix()
        assert isinstance(created["created_at"], int) and created["created_at"] > 1_600_000_000

        assert_error(c.post("/api/libraries", json={"path": str(lib)}), 409, "ALREADY_EXISTS")
        assert_error(c.post("/api/libraries", json={"path": lib.as_posix()}), 409, "ALREADY_EXISTS")
        assert_error(c.post("/api/libraries", json={"path": str(lib / "sub" / "..")}), 409, "ALREADY_EXISTS")
        (lib / "sub").mkdir()
        assert_error(c.post("/api/libraries", json={"path": str(lib / "sub")}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": str(tmp_path)}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": "relative/dir"}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": "."}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": str(tmp_path / "missing")}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": str(tmp_path / "file.txt")}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": "\0bad"}), 422, "INVALID_PATH")
        assert_error(c.post("/api/libraries", json={"path": ""}), 422, "VALIDATION_ERROR")
        assert_error(c.post("/api/libraries", json={}), 422, "VALIDATION_ERROR")
        assert_error(c.post("/api/libraries", json={"path": 5}), 422, "VALIDATION_ERROR")
        assert_error(c.post("/api/libraries", content=b"not json",
                            headers={"content-type": "application/json"}), 422, "VALIDATION_ERROR")

        second = add_library(c, other)
        listed = c.get("/api/libraries").json()
        assert_shape(listed, {"type": "array", "items": {"$ref": "Library"}})
        assert [x["id"] for x in listed] == [created["id"], second["id"]]

        r = c.delete(f"/api/libraries/{created['id']}")
        assert r.status_code == 204 and r.content == b""
        assert_error(c.delete(f"/api/libraries/{created['id']}"), 404, "NOT_FOUND")
        assert_error(c.delete("/api/libraries/abc"), 422, "VALIDATION_ERROR")
        assert [x["id"] for x in c.get("/api/libraries").json()] == [second["id"]]
        drain(app, c)


def test_assets_list_filters_order_paging(daemon, lib, tmp_path):
    for name in ("b.obj", "B.fbx", "a.blend", "apple.obj", "Crate.BLEND", "sub/crate.obj"):
        write(lib / name, blend("4.2") if name.lower().endswith("blend") else b"x")
    other = tmp_path / "other"
    write(other / "zzz.obj")
    with daemon() as (app, c):
        l1 = add_library(c, lib)
        l2 = add_library(c, other)
        drain(app, c)
        r = c.get("/api/assets")
        assert r.status_code == 200
        page = r.json()
        assert_shape(page, "AssetPage")
        names = [(a["name"], a["id"]) for a in page["items"]]
        assert names == sorted(names) and page["total"] == 7
        assert [a["name"] for a in page["items"]] == ["B", "Crate", "a", "apple", "b", "crate", "zzz"]

        assert {a["name"] for a in items(c, library_id=l2["id"])} == {"zzz"}
        assert len(items(c, library_id=l1["id"])) == 6
        assert items(c, library_id=999) == []
        assert {a["name"] for a in items(c, q="CRATE")} == {"Crate", "crate"}
        assert {a["name"] for a in items(c, q="pp")} == {"apple"}
        assert items(c, q="%") == [] and items(c, q="_") == []
        assert {a["ext"] for a in items(c)} == {"obj", "fbx", "blend"}
        assert {a["name"] for a in items(c, ext="blend")} == {"a", "Crate"}
        assert {a["name"] for a in items(c, ext="BLEND")} == {"a", "Crate"}
        assert items(c, ext=".blend") == []
        assert {a["name"] for a in items(c, ext="obj", q="b", library_id=l1["id"])} == {"b"}

        r = c.get("/api/assets", params={"limit": 2, "offset": 1}).json()
        assert [a["name"] for a in r["items"]] == ["Crate", "a"] and r["total"] == 7
        r = c.get("/api/assets", params={"limit": 2, "offset": 6, "ext": "obj"}).json()
        assert r["items"] == [] and r["total"] == 4
        assert len(c.get("/api/assets", params={"limit": 500}).json()["items"]) == 7

        for bad in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "x"}, {"library_id": "abc"}):
            assert_error(c.get("/api/assets", params=bad), 422, "VALIDATION_ERROR")


def test_get_asset(daemon, lib):
    write(lib / "a.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
        r = c.get(f"/api/assets/{a['id']}")
        assert r.status_code == 200 and r.json() == a
        assert_shape(r.json(), "Asset")
        assert a | {"ext": "blend", "name": "a", "dcc_version": "4.2", "status": "ready", "has_thumbnail": True,
                    "has_proxy": False, "poly_count": 1234, "error": None} == a
        assert_error(c.get("/api/assets/999999"), 404, "NOT_FOUND")
        assert_error(c.get("/api/assets/abc"), 422, "VALIDATION_ERROR")


def test_error_envelope_for_unknown_route_and_method(daemon):
    with daemon() as (_app, c):
        assert_error(c.get("/api/nope"), 404, "NOT_FOUND")
        r = c.delete("/api/health")
        assert r.status_code == 405
        assert_shape(r.json(), "Error")


def assert_glb(r) -> None:
    assert r.status_code == 200 and r.headers["content-type"] == "model/gltf-binary"
    magic, version, length = struct.unpack("<4sII", r.content[:12])
    assert (magic, version, length) == (b"glTF", 2, len(r.content))
    chunk_len, chunk_type = struct.unpack("<I4s", r.content[12:20])
    assert chunk_type == b"JSON" and json.loads(r.content[20:20 + chunk_len])["asset"]["version"] == "2.0"


V_VALUES = ({}, {"v": "1700000000"}, {"v": "x"}, {"v": "../../../etc/passwd"}, {"v": ""})


def test_conversion_end_to_end_serves_glb_and_png(daemon, lib, spawn_log):
    write(lib / "m.obj", b"o 1")
    write(lib / "f.fbx", b"fbx")
    write(lib / "a.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        assert sorted(spawned(spawn_log)) == [("a.blend", "thumb"), ("f.fbx", "full"), ("m.obj", "full")]
        for name in ("m.obj", "f.fbx"):
            a = got[name]
            assert a | {"status": "ready", "has_proxy": True, "has_thumbnail": True, "poly_count": 1234,
                        "error": None} == a
            proxy = c.get(f"/api/assets/{a['id']}/proxy")
            assert_glb(proxy)
            thumb = c.get(f"/api/assets/{a['id']}/thumbnail")
            assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/png"
            assert thumb.content.startswith(PNG_SIG)
            for params in V_VALUES:
                assert c.get(f"/api/assets/{a['id']}/proxy", params=params).content == proxy.content
                assert c.get(f"/api/assets/{a['id']}/thumbnail", params=params).content == thumb.content
        b = got["a.blend"]
        assert b | {"status": "ready", "has_proxy": False, "has_thumbnail": True, "poly_count": 1234,
                    "error": None, "dcc_version": "4.2"} == b
        for params in V_VALUES:
            assert_error(c.get(f"/api/assets/{b['id']}/proxy", params=params), 404, "NOT_FOUND")
            assert c.get(f"/api/assets/{b['id']}/thumbnail", params=params).status_code == 200
        cache = lib / ".cache"
        ids = sorted([got["m.obj"]["id"], got["f.fbx"]["id"]])
        listing = sorted([f"{i}.{e}" for i in ids for e in ("glb", "png")] + [f"{b['id']}.png"])
        assert sorted(p.name for p in cache.iterdir()) == listing
        if os.name == "nt":
            assert cache.stat().st_file_attributes & 0x2, ".cache must be hidden on Windows"

        assert c.post(f"/api/assets/{got['m.obj']['id']}/convert").status_code == 202
        drain(app, c)
        assert c.post(f"/api/assets/{b['id']}/convert").status_code == 202
        drain(app, c)
        assert spawned(spawn_log)[3:] == [("m.obj", "full"), ("a.blend", "thumb")]
        assert sorted(p.name for p in cache.iterdir()) == listing
        assert by_name(c)["a.blend"]["has_proxy"] is False
        assert_error(c.post("/api/assets/999999/convert"), 404, "NOT_FOUND")


def test_conversion_failure_and_unsupported(daemon, lib, monkeypatch, spawn_log):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "1")
    write(lib / "a.blend", blend("4.2"))
    write(lib / "p.blend", preview_blend())
    write(lib / "m.obj")
    write(lib / "x.stl")
    write(lib / "y.ma")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        for name in ("a.blend", "m.obj"):
            a = got[name]
            assert (a["status"], a["error"], a["has_proxy"], a["has_thumbnail"]) == ("failed", "fake failure", False,
                                                                                     False)
        assert (got["p.blend"]["status"], got["p.blend"]["has_thumbnail"]) == ("ready", True)
        for name in ("x.stl", "y.ma"):
            a = got[name]
            assert (a["status"], a["error"], a["has_proxy"], a["has_thumbnail"]) == ("unsupported", None, False, False)
            assert_error(c.post(f"/api/assets/{a['id']}/convert"), 409, "UNSUPPORTED")
        for name in ("a.blend", "m.obj", "x.stl", "y.ma"):
            assert_error(c.get(f"/api/assets/{got[name]['id']}/proxy"), 404, "NOT_FOUND")
            assert_error(c.get(f"/api/assets/{got[name]['id']}/thumbnail"), 404, "NOT_FOUND")
        drain(app, c)
        assert sorted(spawned(spawn_log)) == [("a.blend", "thumb"), ("m.obj", "full")]
        assert [p.name for p in (lib / ".cache").iterdir()] == [f"{got['p.blend']['id']}.png"]


def test_no_blender_marks_failed(daemon, lib, monkeypatch):
    monkeypatch.delenv("DAM_BLENDER")
    write(lib / "a.blend", blend("4.2"))
    write(lib / "m.obj")
    write(lib / "p.blend", preview_blend())
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    for name in ("a.blend", "m.obj"):
        assert got[name]["status"] == "failed" and "Blender" in got[name]["error"]
    assert (got["p.blend"]["status"], got["p.blend"]["has_thumbnail"]) == ("ready", True)


def test_blend_with_embedded_preview_never_spawns(daemon, lib, spawn_log):
    sources = {"p.blend": preview_blend("v1"), "l.blend": preview_blend("legacy4"),
               "z.blend": zstd.compress(preview_blend("legacy8"))}
    for name, data in sources.items():
        write(lib / name, data)
    with daemon() as (app, c):
        with c.websocket_connect("/ws/events") as ws:
            add_library(c, lib)
            drain(app, c)
            got = by_name(c)
            assert {a["dcc_version"] for a in got.values()} == {"5.2", "4.4"}
            for a in got.values():
                assert a | {"status": "ready", "has_thumbnail": True, "has_proxy": False, "poly_count": None,
                            "error": None} == a
                for params in V_VALUES:
                    r = c.get(f"/api/assets/{a['id']}/thumbnail", params=params)
                    assert r.headers["content-type"] == "image/png"
                    assert decode_png(r.content) == (2, 2, EXPECTED_2X2)
                    assert_error(c.get(f"/api/assets/{a['id']}/proxy", params=params), 404, "NOT_FOUND")
            pid = got["p.blend"]["id"]
            assert c.post(f"/api/assets/{pid}/convert").status_code == 202
            (lib / ".cache" / f"{pid}.png").unlink()
            assert_error(c.get(f"/api/assets/{pid}/thumbnail"), 404, "NOT_FOUND")
            assert decode_png(c.get(f"/api/assets/{pid}/thumbnail").content)[:2] == (2, 2)
            shutil.rmtree(lib / ".cache")
            assert_error(c.get(f"/api/assets/{pid}/thumbnail"), 404, "NOT_FOUND")
            assert c.get(f"/api/assets/{pid}/thumbnail").status_code == 200
            assert c.get(f"/api/assets/{pid}").json() == got["p.blend"]
            write(lib / "zz_sentinel.stl")
            events = ws_until(ws, lambda ev: any(e["data"]["name"] == "zz_sentinel" for e in ev))
            drain(app, c)
        assert {e["type"] for e in events} == {"asset.added", "asset.updated"}
        assert sorted(e["data"]["name"] for e in events if e["type"] == "asset.added") == ["l", "p", "z", "zz_sentinel"]
        for e in events:
            assert_shape(e, "Event")
            if e["data"]["ext"] == "blend":
                assert e["data"]["status"] == "ready" and e["data"]["has_proxy"] is False
        assert spawns(spawn_log) == 0
        assert sorted(p.name for p in (lib / ".cache").iterdir()) == [f"{pid}.png"]


def test_zstd_preview_without_decoder_falls_back_to_job(daemon, lib, monkeypatch, spawn_log):
    import compression

    monkeypatch.delattr(compression, "zstd")
    monkeypatch.setitem(sys.modules, "compression.zstd", None)
    write(lib / "z.blend", zstd.compress(preview_blend("v1")))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
    assert spawned(spawn_log) == [("z.blend", "thumb")]
    assert a | {"status": "ready", "has_thumbnail": True, "has_proxy": False, "poly_count": 1234} == a


def test_legacy_blend_glb_is_removed_on_restart(daemon, lib, spawn_log, tmp_path):
    write(lib / "p.blend", preview_blend())
    write(lib / "h.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    assert spawned(spawn_log) == [("h.blend", "thumb")]
    cache = lib / ".cache"
    for a in got.values():
        (cache / f"{a['id']}.glb").write_bytes(b"glTF legacy")
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    with conn:
        conn.execute("UPDATE versions SET has_proxy = 1, proxy_poly_count = 5")
    conn.close()
    with daemon() as (app, c):
        drain(app, c)
        after = by_name(c)
        for a in after.values():
            assert (a["status"], a["has_proxy"], a["has_thumbnail"]) == ("ready", False, True)
            assert_error(c.get(f"/api/assets/{a['id']}/proxy"), 404, "NOT_FOUND")
        drain(app, c)
    assert not list(cache.glob("*.glb"))
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    assert conn.execute("SELECT has_proxy, proxy_poly_count FROM versions v JOIN assets a"
                        " ON a.current_version_id = v.id").fetchall() == [(0, None)] * 2
    conn.close()
    assert spawned(spawn_log) == [("h.blend", "thumb")] * 2


def test_cache_serving_rejects_escapes(daemon, lib, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "1")
    outside = write(tmp_path / "outside" / "secret.png", b"\x89PNG secret")
    write(lib / "m.obj")
    write(lib / "x.stl")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        aid, sid = got["m.obj"]["id"], got["x.stl"]["id"]
        assert got["m.obj"]["status"] == "failed"
        cache = lib / ".cache"
        write(cache / f"{sid}.png", b"\x89PNG not served")
        write(cache / f"{sid}.glb", b"glTF not served")
        assert_error(c.get(f"/api/assets/{sid}/thumbnail"), 404, "NOT_FOUND")
        assert_error(c.get(f"/api/assets/{sid}/proxy"), 404, "NOT_FOUND")
        for f in cache.iterdir():
            f.unlink()
        for path in (f"/api/assets/{aid}/../../../outside/secret.png", "/api/assets/..%2F..%2Fsecret/thumbnail",
                     "/api/assets/-1/thumbnail", "/api/assets/0/proxy", "/api/assets/1e3/proxy",
                     "/api/assets/%2e%2e/proxy", f"/api/assets/{aid}/thumbnail?v=../../outside/secret.png"):
            r = c.get(path)
            assert r.status_code in (404, 422) and b"secret" not in r.content, path
            assert_shape(r.json(), "Error")
        try:
            os.symlink(outside, cache / f"{aid}.png")
        except OSError:
            symlinked = False
        else:
            symlinked = True
            assert_error(c.get(f"/api/assets/{aid}/thumbnail"), 404, "NOT_FOUND")
            (cache / f"{aid}.png").unlink()
        cache.rmdir()
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside.parent), str(cache))
        elif not symlinked:
            pytest.skip("cannot create symlinks here")
        else:
            os.symlink(outside.parent, cache)
        os.replace(outside, outside.parent / f"{aid}.png")
        assert_error(c.get(f"/api/assets/{aid}/thumbnail"), 404, "NOT_FOUND")
        assert_error(c.get(f"/api/assets/{aid}/thumbnail", params={"v": "1"}), 404, "NOT_FOUND")
        drain(app, c)


def test_apps_listing(daemon, blender_install):
    with daemon() as (_app, c):
        r = c.get("/api/apps")
    assert r.status_code == 200
    assert_shape(r.json(), {"type": "array", "items": {"$ref": "App"}})
    assert r.json() == [
        {"id": "blender-4.2", "name": "Blender 4.2", "version": "4.2", "executable": blender_install[0],
         "exts": ["blend"]},
        {"id": "blender-5.0", "name": "Blender 5.0", "version": "5.0", "executable": blender_install[1],
         "exts": ["blend"]},
    ]


def test_apps_empty_when_nothing_detected(daemon):
    with daemon() as (_app, c):
        assert c.get("/api/apps").json() == []


def test_launch(daemon, lib, blender_install, popen):
    write(lib / "a.blend", blend("4.2"))
    write(lib / "m.obj")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        a, obj = got["a.blend"], got["m.obj"]

        r = c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-5.0"})
        assert r.status_code == 202 and r.content == b""
        assert len(popen) == 1
        args, kwargs = popen[0]
        assert args == ([blender_install[1], a["path"]],)
        assert not kwargs.get("shell") and not isinstance(args[0], str)

        assert_error(c.post("/api/launch", json={"asset_id": 999999, "app_id": "blender-4.2"}), 404, "NOT_FOUND")
        for app_id in ("maya-2025", "blender", "blender-4.2; calc.exe", "../blender-4.2", blender_install[0]):
            assert_error(c.post("/api/launch", json={"asset_id": a["id"], "app_id": app_id}), 404, "NOT_FOUND")
        assert_error(c.post("/api/launch", json={"asset_id": obj["id"], "app_id": "blender-4.2"}), 409, "UNSUPPORTED")
        for body in ({}, {"asset_id": a["id"]}, {"app_id": "blender-4.2"}, {"asset_id": "abc", "app_id": "x"},
                     {"asset_id": a["path"], "app_id": "blender-4.2"}):
            assert_error(c.post("/api/launch", json=body), 422, "VALIDATION_ERROR")
        assert len(popen) == 1

        (lib / "a.blend").unlink()
        r = c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-4.2"})
        assert_error(r, 404, "NOT_FOUND")
        assert len(popen) == 1
        drain(app, c)


def test_launch_failure_envelope(daemon, lib, blender_install, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "not found", a[0][0])

    monkeypatch.setattr(dcc.subprocess, "Popen", boom)
    write(lib / "a.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        r = c.post("/api/launch", json={"asset_id": items(c)[0]["id"], "app_id": "blender-4.2"})
        assert_error(r, 500, "LAUNCH_FAILED")
        drain(app, c)


def test_launch_with_real_popen_uses_argv_list(daemon, lib, blender_install, monkeypatch):
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **kw):
            seen.append((args, kw))
            super().__init__([sys.executable, "-c", "pass"], *a, **kw)

    monkeypatch.setattr(dcc.subprocess, "Popen", Spy)
    write(lib / "a b & c.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
        assert c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-4.2"}).status_code == 202
        drain(app, c)
    assert seen[0][0] == [blender_install[0], a["path"]] and not seen[0][1].get("shell")


def test_ws_events_deliver_full_assets(daemon, lib):
    write(lib / "a.obj", b"o 1")
    with daemon() as (app, c):
        with c.websocket_connect("/ws/events") as ws:
            library = add_library(c, lib)
            events = ws_until(ws, lambda ev: any(e["type"] == "conversion.done" for e in ev))
            types = [e["type"] for e in events]
            assert types.index("asset.added") < types.index("conversion.started") < types.index("conversion.done")
            for e in events:
                assert_shape(e, "Event")
                assert e["asset_id"] == e["data"]["id"]
            by_type = {e["type"]: e["data"] for e in events}
            assert by_type["asset.added"]["name"] == "a"
            assert by_type["conversion.started"]["status"] == "converting"
            done = by_type["conversion.done"]
            assert done | {"status": "ready", "has_proxy": True, "has_thumbnail": True} == done
            assert done == c.get(f"/api/assets/{done['id']}").json()

            write(lib / "b.stl")
            ev = ws_until(ws, lambda ev: any(e["type"] == "asset.added" for e in ev))
            assert ev[-1]["data"]["name"] == "b" and ev[-1]["data"]["status"] == "unsupported"

            os.utime(lib / "a.obj", (1_700_000_000, 1_700_000_000))
            ev = ws_until(ws, lambda ev: any(e["type"] == "asset.updated" for e in ev))
            upd = next(e for e in ev if e["type"] == "asset.updated")
            assert_shape(upd, "Event")
            assert upd["data"]["mtime"] == 1_700_000_000

            if not any(e["type"] == "conversion.done" for e in ev):
                ws_until(ws, lambda ev: any(e["type"] == "conversion.done" for e in ev))

            (lib / "b.stl").unlink()
            ev = ws_until(ws, lambda ev: any(e["type"] == "asset.removed" for e in ev))
            gone = next(e for e in ev if e["type"] == "asset.removed")
            assert_shape(gone, "Event")
            assert gone["data"]["name"] == "b"

            assert c.delete(f"/api/libraries/{library['id']}").status_code == 204
            ev = ws_until(ws, lambda ev: any(e["type"] == "asset.removed" for e in ev))
            assert next(e for e in ev if e["type"] == "asset.removed")["data"]["name"] == "a"
        drain(app, c)


def test_ws_conversion_failed_event(daemon, lib, monkeypatch):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "1")
    write(lib / "a.blend", blend("4.2"))
    with daemon() as (app, c):
        with c.websocket_connect("/ws/events") as ws:
            add_library(c, lib)
            ev = ws_until(ws, lambda ev: any(e["type"] == "conversion.failed" for e in ev))
            failed = ev[-1]
            assert_shape(failed, "Event")
            assert [e["type"] for e in ev] == ["asset.added", "conversion.started", "conversion.failed"]
            assert failed["data"] | {"status": "failed", "error": "fake failure", "has_thumbnail": False,
                                     "has_proxy": False} == failed["data"]
        drain(app, c)


def test_ws_two_clients_both_receive(daemon, lib):
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        with c.websocket_connect("/ws/events") as w1, c.websocket_connect("/ws/events") as w2:
            write(lib / "x.obj")
            for ws in (w1, w2):
                assert ws_until(ws, lambda ev: bool(ev))[0]["type"] == "asset.added"
        wait_until(lambda: not app.state.clients)
        drain(app, c)
