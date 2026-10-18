import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from conftest import (
    BLENDER,
    PNG_SIG,
    ROOT,
    add_library,
    by_name,
    decode_png,
    drain,
    no_fake_procs,
)
from fastapi.testclient import TestClient

from backend import indexer
from backend.app import create_app
from backend.blend_header import parse_blend_header
from backend.blend_preview import read_embedded_preview, rgba_to_png
from backend.worker import Worker

SCRIPT = str(ROOT / "scripts" / "blender_export_proxy.py")
META_KEYS = {"ok", "blender_version", "poly_count", "proxy_poly_count", "bounds", "thumb_size", "error"}
SUZANNE_TRIS = 15744
pytestmark = pytest.mark.blender


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def copy_in(fixtures: Path, name: str, tmp_path: Path, as_name: str | None = None) -> Path:
    src = tmp_path / "in" / (as_name or name)
    src.parent.mkdir(exist_ok=True)
    shutil.copy2(fixtures / name, src)
    return src


def run_script(src: Path, tmp_path: Path, glb: bool, thumb: int = 160) -> tuple[int, dict | None, Path]:
    out = tmp_path / "out"
    out.mkdir()
    before = (md5(src), sorted(p.name for p in src.parent.iterdir()))
    argv = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P", SCRIPT, "--", "--input", str(src),
            "--out-png", str(out / "t.png"), "--out-meta", str(out / "m.json"), "--thumb-size", str(thumb)]
    rc = subprocess.run(argv + (["--out-glb", str(out / "p.glb")] if glb else []), timeout=300, capture_output=True,
                        check=False).returncode
    assert (md5(src), sorted(p.name for p in src.parent.iterdir())) == before, "input file or folder was modified"
    meta = json.loads((out / "m.json").read_text(encoding="utf-8")) if (out / "m.json").exists() else None
    return rc, meta, out


def assert_meta_shape(meta: dict | None, thumb: int) -> None:
    assert meta is not None and set(meta) == META_KEYS, meta
    assert re.fullmatch(r"\d+\.\d+\.\d+", meta["blender_version"]), meta["blender_version"]
    assert meta["thumb_size"] == thumb and isinstance(meta["ok"], bool)


def png_dims(data: bytes) -> tuple[int, int]:
    assert data[:8] == PNG_SIG and data[12:16] == b"IHDR"
    w, h, depth, color = struct.unpack(">IIBB", data[16:26])
    assert (depth, color) == (8, 6)
    return w, h


def glb_json(data: bytes) -> dict:
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert (magic, version, length) == (b"glTF", 2, len(data))
    jlen, jtype = struct.unpack("<I4s", data[12:20])
    blen, btype = struct.unpack("<I4s", data[20 + jlen:28 + jlen])
    assert (jtype, btype) == (b"JSON", b"BIN\0") and 28 + jlen + blen == len(data)
    gltf = json.loads(data[20:20 + jlen])
    assert "KHR_draco_mesh_compression" in gltf["extensionsUsed"]
    assert "KHR_draco_mesh_compression" in gltf["extensionsRequired"]
    prims = [p for m in gltf["meshes"] for p in m["primitives"]]
    assert prims and all("KHR_draco_mesh_compression" in p["extensions"] for p in prims)
    return gltf


def test_fixtures_have_expected_previews_and_headers(real_fixtures):
    for name in ("preview.blend", "preview_zstd.blend", "camera.blend", "empty_scene.blend"):
        header = parse_blend_header(str(real_fixtures / name))
        assert header and header["version"] == "5.2" and header["header_format"] == "v1", name
        assert header["compressed"] == ("zstd" if "zstd" in name else None), name
        w, h, rgba = read_embedded_preview(str(real_fixtures / name))
        assert (w, h) == (128, 128) and len(rgba) == 128 * 128 * 4
        assert decode_png(rgba_to_png(w, h, rgba)) == (w, h, rgba)
        assert any(rgba[3::4]), "preview is fully transparent"
    plain = read_embedded_preview(str(real_fixtures / "preview.blend"))
    assert read_embedded_preview(str(real_fixtures / "preview_zstd.blend")) == plain
    for name in ("nopreview.blend", "truncated.blend", "empty.blend"):
        assert read_embedded_preview(str(real_fixtures / name)) is None, name


@pytest.mark.parametrize("name", ["suzanne.obj", "suzanne.fbx", "edit_mode.blend", "sculpt_mode.blend"])
def test_full_proxy(real_fixtures, tmp_path, name):
    rc, meta, out = run_script(copy_in(real_fixtures, name, tmp_path), tmp_path, glb=True)
    assert rc == 0, meta
    assert_meta_shape(meta, 160)
    assert meta["ok"] is True and meta["error"] is None
    assert meta["poly_count"] == SUZANNE_TRIS and 0 < meta["proxy_poly_count"] < meta["poly_count"]
    lo, hi = meta["bounds"]["min"], meta["bounds"]["max"]
    assert len(lo) == len(hi) == 3 and all(a < b for a, b in zip(lo, hi))
    glb_json((out / "p.glb").read_bytes())
    assert png_dims((out / "t.png").read_bytes()) == (160, 160)
    assert sorted(p.name for p in out.iterdir()) == ["m.json", "p.glb", "t.png"]


@pytest.mark.parametrize(("name", "tris"), [("preview.blend", 12), ("camera.blend", 12),
                                            ("nopreview.blend", SUZANNE_TRIS)])
def test_blend_thumbnail_only(real_fixtures, tmp_path, name, tris):
    rc, meta, out = run_script(copy_in(real_fixtures, name, tmp_path), tmp_path, glb=False, thumb=96)
    assert rc == 0, meta
    assert_meta_shape(meta, 96)
    assert (meta["ok"], meta["error"], meta["poly_count"], meta["proxy_poly_count"]) == (True, None, tris, None)
    assert parse_blend_header(str(real_fixtures / name))["version"] == ".".join(meta["blender_version"].split(".")[:2])
    assert png_dims((out / "t.png").read_bytes()) == (96, 96)
    assert sorted(p.name for p in out.iterdir()) == ["m.json", "t.png"]


@pytest.mark.parametrize(("name", "as_name"), [("empty_scene.blend", None), ("truncated.blend", None),
                                               ("empty.blend", None), ("suzanne.obj", "suzanne.stl")])
def test_failures_exit_nonzero_with_meta(real_fixtures, tmp_path, name, as_name):
    rc, meta, out = run_script(copy_in(real_fixtures, name, tmp_path, as_name), tmp_path, glb=True)
    assert rc != 0
    assert_meta_shape(meta, 160)
    assert meta["ok"] is False and isinstance(meta["error"], str) and meta["error"]
    assert meta["proxy_poly_count"] is None and not (out / "p.glb").exists()


@pytest.fixture
def real_daemon(tmp_path, monkeypatch):
    for k in ("DAM_BLENDER", "DAM_DB", "FAKE_BLENDER_LOG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DAM_WORKERS", "2")
    monkeypatch.setenv("DAM_JOB_TIMEOUT", "180")
    monkeypatch.setattr(indexer, "DEBOUNCE", 0.2)
    calls: list[list[str]] = []
    real_run = Worker._run

    async def spy(self, argv):
        calls.append(argv)
        return await real_run(self, argv)

    monkeypatch.setattr(Worker, "_run", spy)
    with TestClient(create_app(str(tmp_path / "real.sqlite"))) as c:
        yield c.app, c, calls


def test_real_daemon_end_to_end(real_fixtures, real_daemon, tmp_path):
    app, c, calls = real_daemon
    lib = tmp_path / "lib"
    lib.mkdir()
    names = ["preview.blend", "nopreview.blend", "suzanne.obj"]
    for n in names:
        shutil.copy2(real_fixtures / n, lib / n)
    sums = {n: md5(lib / n) for n in names}
    apps = c.get("/api/apps").json()
    assert any(a["executable"] == BLENDER and a["exts"] == ["blend"] for a in apps), apps
    add_library(c, lib.resolve())
    drain(app, c)
    got = by_name(c)
    p, n, o = got["preview.blend"], got["nopreview.blend"], got["suzanne.obj"]
    for a, tris in ((p, 12), (n, SUZANNE_TRIS), (o, SUZANNE_TRIS)):
        assert a | {"status": "ready", "has_thumbnail": True, "has_proxy": True, "poly_count": tris,
                    "error": None} == a
        assert png_dims(c.get(f"/api/assets/{a['id']}/thumbnail", params={"v": a["mtime"]}).content) == (512, 512)
        r = c.get(f"/api/assets/{a['id']}/proxy", params={"v": a["mtime"]})
        assert r.headers["content-type"] == "model/gltf-binary"
        glb_json(r.content)
    assert p["dcc_version"] == n["dcc_version"] == "5.2"

    by_input = {Path(argv[argv.index("--input") + 1]).name: argv for argv in calls}
    assert sorted(by_input) == sorted(names) and len(calls) == 3
    for argv in by_input.values():
        assert argv[:8] == [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1",
                            "-P", SCRIPT, "--"]
        assert "--out-glb" in argv
    cache = sorted(f.name for f in (lib / ".cache").iterdir())
    assert cache == sorted(f"{a['id']}.{e}" for a in (p, n, o) for e in ("png", "glb"))
    assert {name: md5(lib / name) for name in names} == sums
    assert no_fake_procs(timeout=30, image="blender%", needle="blender_export_proxy")
