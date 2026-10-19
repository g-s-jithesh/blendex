import itertools
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
    wait_until,
    ws_until,
)

from backend import dcc
from backend.indexer import EXTS

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


def patch_popen(monkeypatch, fake) -> None:
    monkeypatch.setattr(dcc, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": fake}))


@pytest.fixture
def popen(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    patch_popen(monkeypatch, fake)
    return calls


def test_every_spec_endpoint_is_routed_and_nothing_else(daemon):
    spec = {(m.upper(), "/api" + p.replace("{id}", "{x}")) for p, ops in SPEC["paths"].items()
            for m in ops if m != "parameters"}
    with daemon() as (app, _c):
        routes = set()
        for r in app.routes:
            if r.path in DOC_ROUTES or r.path == "/ws/events":
                continue
            path = re.sub(r"\{\w+_id\}", "{x}", r.path)
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
        (lib / "sub").mkdir()
        assert_error(c.post("/api/libraries", json={"path": str(lib / "sub" / "..")}), 409, "ALREADY_EXISTS")
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
                    "has_proxy": True, "poly_count": 1234, "error": None} == a
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
        assert sorted(spawned(spawn_log)) == [("a.blend", "full"), ("f.fbx", "full"), ("m.obj", "full")]
        assert got["a.blend"]["dcc_version"] == "4.2"
        for name in ("m.obj", "f.fbx", "a.blend"):
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
        cache = lib / ".cache"
        listing = sorted(f"{a['id']}.{e}" for a in got.values() for e in ("glb", "png"))
        assert sorted(p.name for p in cache.iterdir()) == listing
        if os.name == "nt":
            assert cache.stat().st_file_attributes & 0x2, ".cache must be hidden on Windows"

        assert c.post(f"/api/assets/{got['m.obj']['id']}/convert").status_code == 202
        drain(app, c)
        assert c.post(f"/api/assets/{b['id']}/convert").status_code == 202
        drain(app, c)
        assert spawned(spawn_log)[3:] == [("m.obj", "full"), ("a.blend", "full")]
        assert sorted(p.name for p in cache.iterdir()) == listing
        assert by_name(c)["a.blend"]["has_proxy"] is True
        assert_error(c.post("/api/assets/999999/convert"), 404, "NOT_FOUND")


def test_conversion_failure_and_unsupported(daemon, lib, monkeypatch, spawn_log):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "1")
    write(lib / "a.blend", blend("4.2"))
    write(lib / "p.blend", preview_blend())
    write(lib / "m.obj")
    write(lib / "x.stl")
    write(lib / "y.ply")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        for name in ("a.blend", "m.obj"):
            a = got[name]
            assert (a["status"], a["error"], a["has_proxy"], a["has_thumbnail"]) == ("failed", "fake failure", False,
                                                                                     False)
        p = got["p.blend"]
        assert (p["status"], p["error"], p["has_proxy"], p["has_thumbnail"]) == ("failed", "fake failure", False, True)
        assert c.get(f"/api/assets/{p['id']}/thumbnail").status_code == 200
        assert_error(c.get(f"/api/assets/{p['id']}/proxy"), 404, "NOT_FOUND")
        for name in ("x.stl", "y.ply"):
            a = got[name]
            assert (a["status"], a["error"], a["has_proxy"], a["has_thumbnail"]) == ("unsupported", None, False, False)
            assert_error(c.post(f"/api/assets/{a['id']}/convert"), 409, "UNSUPPORTED")
        for name in ("a.blend", "m.obj", "x.stl", "y.ply"):
            assert_error(c.get(f"/api/assets/{got[name]['id']}/proxy"), 404, "NOT_FOUND")
            assert_error(c.get(f"/api/assets/{got[name]['id']}/thumbnail"), 404, "NOT_FOUND")
        drain(app, c)
        assert sorted(spawned(spawn_log)) == [("a.blend", "full"), ("m.obj", "full"), ("p.blend", "full")]
        assert [f.name for f in (lib / ".cache").iterdir()] == [f"{p['id']}.png"]


def test_no_blender_marks_failed(daemon, lib, monkeypatch):
    monkeypatch.delenv("DAM_BLENDER")
    write(lib / "a.blend", blend("4.2"))
    write(lib / "m.obj")
    write(lib / "p.blend", preview_blend())
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    for name in ("a.blend", "m.obj", "p.blend"):
        assert got[name]["status"] == "failed" and "Blender" in got[name]["error"]
    assert got["p.blend"]["has_thumbnail"] is True


def test_blend_embedded_preview_shows_before_full_job(daemon, lib, monkeypatch, spawn_log):
    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "3")
    sources = {"p.blend": preview_blend("v1"), "l.blend": preview_blend("legacy4"),
               "z.blend": zstd.compress(preview_blend("legacy8"))}
    for name, data in sources.items():
        write(lib / name, data)
    with daemon() as (app, c), c.websocket_connect("/ws/events") as ws:
        add_library(c, lib)
        wait_until(lambda: len(items(c)) == 3 and all(a["has_thumbnail"] for a in items(c)))
        early = by_name(c)
        assert {a["dcc_version"] for a in early.values()} == {"5.2", "4.4"}
        for a in early.values():
            assert a["status"] in ("pending", "converting") and a["has_proxy"] is False
            for params in V_VALUES:
                r = c.get(f"/api/assets/{a['id']}/thumbnail", params=params)
                assert r.headers["content-type"] == "image/png"
                assert decode_png(r.content) == (2, 2, EXPECTED_2X2)
        drain(app, c)
        got = by_name(c)
        for a in got.values():
            assert a | {"status": "ready", "has_thumbnail": True, "has_proxy": True, "poly_count": 1234,
                        "error": None} == a
            assert_glb(c.get(f"/api/assets/{a['id']}/proxy"))
            assert decode_png(c.get(f"/api/assets/{a['id']}/thumbnail").content)[:2] == (1, 1)
        pid = got["p.blend"]["id"]
        (lib / ".cache" / f"{pid}.png").unlink()
        assert_error(c.get(f"/api/assets/{pid}/thumbnail"), 404, "NOT_FOUND")
        assert decode_png(c.get(f"/api/assets/{pid}/thumbnail").content) == (2, 2, EXPECTED_2X2)
        drain(app, c)
        assert c.get(f"/api/assets/{pid}").json()["has_proxy"] is True
        write(lib / "zz_sentinel.stl")
        events = ws_until(ws, lambda ev: any(e["data"]["name"] == "zz_sentinel" for e in ev))
    added = {e["data"]["name"]: e["data"] for e in events if e["type"] == "asset.added"}
    assert sorted(added) == ["l", "p", "z", "zz_sentinel"]
    for n in "lpz":
        assert (added[n]["has_thumbnail"], added[n]["has_proxy"], added[n]["status"]) == (True, False, "pending")
    assert [e["type"] for e in events].count("conversion.done") == 4
    for e in events:
        assert_shape(e, "Event")
    assert sorted(spawned(spawn_log)) == [("l.blend", "full"), ("p.blend", "full"), ("p.blend", "full"),
                                          ("z.blend", "full")]


def test_zstd_blend_without_decoder_still_converts(daemon, lib, monkeypatch, spawn_log):
    import compression

    monkeypatch.delattr(compression, "zstd")
    monkeypatch.setitem(sys.modules, "compression.zstd", None)
    write(lib / "z.blend", zstd.compress(preview_blend("v1")))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
    assert spawned(spawn_log) == [("z.blend", "full")]
    assert a | {"status": "ready", "has_thumbnail": True, "has_proxy": True, "poly_count": 1234} == a


def test_image_only_blends_get_proxies_on_restart(daemon, lib, spawn_log, tmp_path):
    write(lib / "p.blend", preview_blend())
    write(lib / "h.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
    assert sorted(spawned(spawn_log)) == [("h.blend", "full"), ("p.blend", "full")]
    for f in (lib / ".cache").glob("*.glb"):
        f.unlink()
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    with conn:
        conn.execute("UPDATE versions SET has_proxy = 0, proxy_poly_count = NULL")
    conn.close()
    with daemon() as (app, c):
        drain(app, c)
        for a in by_name(c).values():
            assert (a["status"], a["has_proxy"], a["has_thumbnail"]) == ("ready", True, True)
            assert_glb(c.get(f"/api/assets/{a['id']}/proxy"))
    assert sorted(spawned(spawn_log)) == [("h.blend", "full")] * 2 + [("p.blend", "full")] * 2


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


def test_conversion_refuses_to_write_through_a_linked_cache(daemon, lib, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(lib / ".cache"))
    else:
        os.symlink(outside, lib / ".cache")
    write(lib / "m.obj")
    write(lib / "p.blend", preview_blend())
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    for name in ("m.obj", "p.blend"):
        assert got[name]["status"] == "failed" and "links to another folder" in got[name]["error"], got[name]
        assert got[name]["has_thumbnail"] is False and got[name]["has_proxy"] is False
    assert list(outside.iterdir()) == []


def test_apps_listing(daemon, blender_install):
    with daemon() as (_app, c):
        r = c.get("/api/apps")
    assert r.status_code == 200
    assert_shape(r.json(), {"type": "array", "items": {"$ref": "App"}})
    blender_exts = ["blend", *dcc.BLENDER_IMPORTS]
    assert [a for a in r.json() if a["id"].startswith("blender")] == [
        {"id": "blender-4.2", "name": "Blender 4.2", "version": "4.2", "executable": blender_install[0],
         "exts": blender_exts},
        {"id": "blender-5.0", "name": "Blender 5.0", "version": "5.0", "executable": blender_install[1],
         "exts": blender_exts},
    ]
    system = [a for a in r.json() if not a["id"].startswith("blender")]
    if sys.platform == "win32":
        assert [(a["id"], a["exts"]) for a in system] == [("explorer", sorted(EXTS)), ("open-with", sorted(EXTS))]
        assert all(os.path.isfile(a["executable"]) for a in system)
    else:
        assert system == []


def test_apps_without_dcc_installs_only_list_system_openers(daemon):
    with daemon() as (_app, c):
        ids = [a["id"] for a in c.get("/api/apps").json()]
    assert ids == (["explorer", "open-with"] if sys.platform == "win32" else [])


def test_launch(daemon, lib, blender_install, popen):
    write(lib / "a.blend", blend("4.2"))
    write(lib / "m.obj")
    write(lib / "r.ma")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
        a, obj, rig = got["a.blend"], got["m.obj"], got["r.ma"]

        r = c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-5.0"})
        assert r.status_code == 202 and r.content == b""
        assert len(popen) == 1
        args, kwargs = popen[0]
        assert args == ([blender_install[1], str(Path(a["path"]))],)
        assert not kwargs.get("shell") and not isinstance(args[0], str)

        assert_error(c.post("/api/launch", json={"asset_id": 999999, "app_id": "blender-4.2"}), 404, "NOT_FOUND")
        for app_id in ("maya-2025", "blender", "blender-4.2; calc.exe", "../blender-4.2", blender_install[0]):
            assert_error(c.post("/api/launch", json={"asset_id": a["id"], "app_id": app_id}), 404, "NOT_FOUND")
        assert_error(c.post("/api/launch", json={"asset_id": rig["id"], "app_id": "blender-4.2"}), 409, "UNSUPPORTED")
        for body in ({}, {"asset_id": a["id"]}, {"app_id": "blender-4.2"}, {"asset_id": "abc", "app_id": "x"},
                     {"asset_id": a["path"], "app_id": "blender-4.2"}):
            assert_error(c.post("/api/launch", json=body), 422, "VALIDATION_ERROR")
        assert len(popen) == 1
        assert c.post("/api/launch", json={"asset_id": obj["id"], "app_id": "blender-4.2"}).status_code == 202
        assert popen[1][0] == ([blender_install[0], "--python", str(dcc.OPEN_IMPORT_SCRIPT), "--",
                                str(Path(obj["path"]))],)
        del popen[1]

        (lib / "a.blend").unlink()
        r = c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-4.2"})
        assert_error(r, 404, "NOT_FOUND")
        assert len(popen) == 1
        drain(app, c)


def test_launch_failure_envelope(daemon, lib, blender_install, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "not found", a[0][0])

    patch_popen(monkeypatch, boom)
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

    patch_popen(monkeypatch, Spy)
    write(lib / "a b & c.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
        assert c.post("/api/launch", json={"asset_id": a["id"], "app_id": "blender-4.2"}).status_code == 202
        drain(app, c)
    assert seen[0][0] == [blender_install[0], str(Path(a["path"]))] and not seen[0][1].get("shell")


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


def queue_seen(**want):
    return lambda ev: any(e["type"] == "queue.updated" and e["data"] | want == e["data"] for e in ev)


def test_ws_queue_updated_events(daemon, lib, monkeypatch):
    monkeypatch.setenv("FAKE_BLENDER_SLEEP", "0.5")
    write(lib / "a.obj", b"o 1")
    with daemon() as (app, c), c.websocket_connect("/ws/events") as ws:
        c.post("/api/queue/pause")
        ev = ws_until(ws, bool, queue=True)
        assert ev == [{"type": "queue.updated", "data": {"queued": 0, "running": 0, "paused": True}}]
        assert_shape(ev[0], "QueueEvent")
        add_library(c, lib)
        ev = ws_until(ws, queue_seen(queued=1), queue=True)
        c.post("/api/queue/resume")
        ev += ws_until(ws, queue_seen(running=1), queue=True)
        ev += ws_until(ws, queue_seen(running=0, paused=False), queue=True)
        queue_events = [e for e in ev if e["type"] == "queue.updated"]
        for e in queue_events:
            assert_shape(e, "QueueEvent")
        assert queue_events[-1]["data"] == {"queued": 0, "running": 0, "paused": False}
        assert all(a != b for a, b in itertools.pairwise(queue_events))
        drain(app, c)


def test_uvicorn_has_a_websocket_implementation():
    import uvicorn

    config = uvicorn.Config("backend.app:create_app", factory=True)
    config.load()
    assert config.ws_protocol_class is not None
