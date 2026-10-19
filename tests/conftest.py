import json
import os
import struct
import subprocess
import sys
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient

from backend import dcc, indexer
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).resolve().parent / "fake_blender.py"
SPEC = json.loads((ROOT / "docs" / "api_spec.json").read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]

HEADERS = {
    "2.90": "42 4C 45 4E 44 45 52 2D 76 32 39 30 52 45 4E 44 48 00 00 00 80 C1 DF B9 23 00 00 00 00 00 00 00",
    "3.6": "42 4C 45 4E 44 45 52 2D 76 33 30 36 47 4C 4F 42 50 04 00 00 60 CD FF 58 BD 00 00 00 9B 00 00 00",
    "4.1": "42 4C 45 4E 44 45 52 2D 76 34 30 31 47 4C 4F 42 50 04 00 00 20 D7 BF 6D 3C 00 00 00 A4 00 00 00",
    "4.2": "42 4C 45 4E 44 45 52 2D 76 34 30 32 47 4C 4F 42 50 04 00 00 F0 D4 DF 4E 7C 00 00 00 AB 00 00 00",
    "4.3": "42 4C 45 4E 44 45 52 2D 76 34 30 33 47 4C 4F 42 50 04 00 00 D0 CE 3F 07 34 00 00 00 AB 00 00 00",
    "4.4": "42 4C 45 4E 44 45 52 2D 76 34 30 34 47 4C 4F 42 50 04 00 00 90 D7 7F F5 AD 00 00 00 AA 00 00 00",
    "5.0": "42 4C 45 4E 44 45 52 31 37 2D 30 31 76 30 35 30 30 47 4C 4F 42 AA 00 00 00 10 00 00 00 00 00 00",
}


def blend(version: str = "4.2", extra: bytes = b"") -> bytes:
    return bytes.fromhex(HEADERS[version]) + extra


BHEADS = {
    "v1": (b"BLENDER17-01v0502", lambda code, n: struct.pack("<4siQqq", code, 0, 0x10, n, 1)),
    "legacy8": (b"BLENDER-v404", lambda code, n: struct.pack("<4siQii", code, n, 0x10, 0, 1)),
    "legacy4": (b"BLENDER_v404", lambda code, n: struct.pack("<4siIii", code, n, 0x10, 0, 1)),
}
PIXELS_2X2 = bytes.fromhex("FF0000FF 00FF00FF 0000FFFF 40404080")
EXPECTED_2X2 = bytes.fromhex("0000FFFF 80808080 FF0000FF 00FF00FF")


def block(kind: str, code: bytes, data: bytes, length: int | None = None) -> bytes:
    return BHEADS[kind][1](code, len(data) if length is None else length) + data


def thumb_payload(w: int, h: int, pixels: bytes | None = None) -> bytes:
    return struct.pack("<ii", w, h) + (PIXELS_2X2 if pixels is None else pixels)


def preview_blend(kind: str = "v1", test: bytes | None = None, head: bytes | None = None) -> bytes:
    return ((BHEADS[kind][0] if head is None else head) + block(kind, b"REND", bytes(264)) * 2
            + block(kind, b"TEST", thumb_payload(2, 2) if test is None else test) + block(kind, b"GLOB", bytes(16)))


PNG_SIG = bytes.fromhex("89504E470D0A1A0A")


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == PNG_SIG
    pos, chunks = 8, []
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body), kind
        chunks.append((kind, body))
        pos += 12 + n
    assert pos == len(data) and chunks[0][0] == b"IHDR" and chunks[-1] == (b"IEND", b"")
    w, h, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (depth, color, comp, filt, interlace) == (8, 6, 0, 0, 0)
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    stride = w * 4 + 1
    assert len(raw) == h * stride and all(raw[y * stride] == 0 for y in range(h))
    return w, h, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(h))


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def shape_errors(value, schema: dict | str, where: str = "$") -> list[str]:
    if isinstance(schema, str):
        schema = {"$ref": schema}
    if "$ref" in schema:
        return shape_errors(value, SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]], where)
    if "const" in schema and value != schema["const"]:
        return [f"{where}: {value!r} != {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} not in {schema['enum']}"]
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if types and not any(_TYPES[t](value) for t in types):
        return [f"{where}: {value!r} is not {types}"]
    errs = []
    if isinstance(value, dict) and "properties" in schema:
        if set(value) != set(schema["properties"]):
            errs.append(f"{where}: keys {sorted(value)} != {sorted(schema['properties'])}")
        for k, sub in schema["properties"].items():
            if k in value:
                errs += shape_errors(value[k], sub, f"{where}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += shape_errors(v, schema["items"], f"{where}[{i}]")
    return errs


def assert_shape(value, schema: dict | str) -> None:
    assert not (errs := shape_errors(value, schema)), errs


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, (resp.status_code, resp.text)
    assert_shape(resp.json(), "Error")
    assert resp.json()["error"]["code"] == code, resp.json()


def wait_until(fn, timeout: float = 15, step: float = 0.1):
    end = time.monotonic() + timeout
    while not (result := fn()):
        if time.monotonic() > end:
            raise AssertionError(f"timed out after {timeout}s waiting for {fn}")
        time.sleep(step)
    return result


def drain(app, c) -> None:
    time.sleep(0.3)
    c.portal.call(app.state.idle)


def settle(app, c) -> None:
    time.sleep(indexer.DEBOUNCE + 0.5)
    c.portal.call(app.state.idle)


def add_library(c, path: Path) -> dict:
    r = c.post("/api/libraries", json={"path": str(path)})
    assert r.status_code == 201, r.text
    return r.json()


def items(c, **params) -> list[dict]:
    return c.get("/api/assets", params={"limit": 500, **params}).json()["items"]


def by_name(c, **params) -> dict[str, dict]:
    return {a["name"] + "." + a["ext"]: a for a in items(c, **params)}


def ws_until(ws, pred, timeout: float = 20, queue: bool = False) -> list[dict]:
    end, seen = time.monotonic() + timeout, []

    async def recv():
        with anyio.fail_after(max(0.01, end - time.monotonic())):
            # TestClient's receive_json has no timeout; a missing event would hang the suite.
            return await ws._send_rx.receive()

    while not pred(seen):
        msg = json.loads(ws.portal.call(recv)["text"])
        if queue or msg["type"] != "queue.updated":
            seen.append(msg)
    return seen


def fake_procs(image: str = "python%", needle: str = "fake_blender") -> list[str]:
    if os.name == "nt":
        q = f"Get-CimInstance Win32_Process -Filter \"Name like '{image}' and CommandLine like '%{needle}%'\""
        cmd = ["powershell", "-NoProfile", "-Command", q + " | ForEach-Object ProcessId"]
    else:
        cmd = ["pgrep", "-f", needle]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False).stdout.split()


def no_fake_procs(timeout: float = 10, image: str = "python%", needle: str = "fake_blender") -> bool:
    end = time.monotonic() + timeout
    while procs := fake_procs(image, needle):
        if time.monotonic() > end:
            raise AssertionError(f"fake blender processes still alive: {procs}")
        time.sleep(0.5)
    return True


@pytest.fixture
def lib(tmp_path) -> Path:
    p = tmp_path / "lib"
    p.mkdir()
    return p.resolve()


@pytest.fixture
def spawn_log(tmp_path) -> Path:
    return tmp_path / "spawns"


def spawns(log: Path) -> int:
    return len(spawned(log))


def spawned(log: Path) -> list[tuple[str, str]]:
    entries = [f.read_text(encoding="utf-8") for f in sorted(log.glob("*.txt"))] if log.exists() else []
    return [(Path(p).name, mode) for p, mode in (entry.split("\t") for entry in entries)]


BLENDERS = sorted((a for a in dcc.detect_apps() if a["id"].startswith("blender")), key=dcc._version_key)
BLENDER = BLENDERS[-1]["executable"] if BLENDERS else None
FIXTURES = ROOT / "tests" / "fixtures" / "out"
FIXTURE_NAMES = ["preview.blend", "preview_zstd.blend", "nopreview.blend", "camera.blend", "empty_scene.blend",
                 "suzanne.obj", "suzanne.fbx", "truncated.blend", "empty.blend", "edit_mode.blend",
                 "sculpt_mode.blend"]


@pytest.fixture(scope="session")
def real_fixtures() -> Path:
    if not BLENDER:
        pytest.skip("no Blender installed")
    gen = ROOT / "tests" / "fixtures" / "make_fixtures.py"
    files = [FIXTURES / n for n in FIXTURE_NAMES]
    if not all(f.is_file() and f.stat().st_mtime >= gen.stat().st_mtime for f in files):
        subprocess.run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(gen), "--",
                        str(FIXTURES)], check=True, timeout=300, capture_output=True)
    return FIXTURES


@pytest.fixture
def daemon(tmp_path, monkeypatch, spawn_log):
    platform = sys.platform if sys.platform in dcc.CANDIDATES else "linux"
    monkeypatch.setitem(dcc.CANDIDATES, platform, [])
    monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which=lambda _: None))
    monkeypatch.setattr(indexer, "DEBOUNCE", 0.2)
    monkeypatch.setenv("DAM_BLENDER", str(FAKE))
    monkeypatch.setenv("DAM_WORKERS", "2")
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(spawn_log))
    for k in ("DAM_DB", "DAM_JOB_TIMEOUT", "FAKE_BLENDER_SLEEP", "FAKE_BLENDER_FAIL"):
        monkeypatch.delenv(k, raising=False)

    @contextmanager
    def start():
        app = create_app(str(tmp_path / "t.sqlite"))
        with TestClient(app) as c:
            yield app, c

    return start
