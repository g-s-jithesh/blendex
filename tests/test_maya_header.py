import glob
import os
import random
import struct
import subprocess
from pathlib import Path

import pytest
from conftest import add_library, by_name, drain

from backend import dcc
from backend.maya_header import parse_maya_header

MAYAPY = sorted(glob.glob("C:/Program Files/Autodesk/Maya*/bin/mayapy.exe"))


def chunk8(tag: bytes, data: bytes) -> bytes:
    return tag + bytes(4) + struct.pack(">Q", len(data)) + data + bytes(-len(data) % 8)


def form8(kind: bytes, body: bytes) -> bytes:
    return b"FOR8" + bytes(4) + struct.pack(">Q", 4 + len(body)) + kind + body


def mb8(*chunks: bytes) -> bytes:
    return form8(b"Maya", form8(b"HEAD", b"".join(chunks)))


def form4(kind: bytes, body: bytes) -> bytes:
    return b"FOR4" + struct.pack(">I", 4 + len(body)) + kind + body


MB_2027 = mb8(chunk8(b"VERS", b"2027"), chunk8(b"UVER", b"undef"), chunk8(b"MADE", b"undef"))
MB_2027_WALK = mb8(chunk8(b"UVER", b"undef"), chunk8(b"MADE", b"undef"), chunk8(b"VERS", b"2027"))
MB_2012 = form4(b"Maya", form4(b"HEAD", b"VERS" + struct.pack(">I", 4) + b"2012" + b"UVER" + struct.pack(">I", 0)))
MA_2027 = b'//Maya ASCII 2027 scene\n//Name: cube.ma\nrequires maya "2027";\ncurrentUnit -l centimeter;\n'


@pytest.fixture
def parse(tmp_path):
    def run(data: bytes, ext: str = "mb"):
        f = tmp_path / f"x.{ext}"
        f.write_bytes(data)
        return parse_maya_header(str(f))

    return run


@pytest.mark.parametrize(("data", "expected"), [
    (MA_2027, "2027"),
    (MA_2027.replace(b"\n", b"\r\n"), "2027"),
    (b"//Maya ASCII 2018ff09 scene\n//Name: a.ma\n", "2018"),
    (b"//Maya ASCII 2016R2 scene\n", "2016"),
    (b"//Maya ASCII 8.5 scene\n", "8.5"),
    (b'//Maya ASCII 2026 scene\nrequires -nodeType "aiOptions" "mtoa" "5.4";\nrequires maya "2027";\n', "2027"),
    (b'//Maya ASCII 2027 scene\nrequires maya "2025ff03";\n', "2025"),
    (b"", None),
    (b"//Maya ASCII", None),
    (b"//Maya ASCII  scene\n", None),
    (b"//Maya ASCII abc scene\n", None),
    (b'requires maya "2027";\n', None),
    (b"\x00\xff garbage" * 100, None),
])
def test_ascii_vectors(parse, data, expected):
    assert parse(data, "ma") == expected


@pytest.mark.parametrize(("data", "expected"), [
    (MB_2027, "2027"),
    (MB_2027_WALK, "2027"),
    (MB_2012, "2012"),
    (mb8(chunk8(b"UVER", b"undef")), None),
    (mb8(chunk8(b"VERS", b"")), None),
    (mb8(chunk8(b"VERS", b"undef")), None),
    (MB_2027.replace(b"HEAD", b"BODY"), None),
    (MB_2027.replace(b"Maya", b"Mayb"), None),
    (b"FOR8", None),
    (b"", None),
])
def test_binary_vectors(parse, data, expected):
    assert parse(data) == expected


def test_binary_vector_layout_matches_real_maya_2027():
    assert MB_2027[:4] + MB_2027[16:20] + MB_2027[20:24] + MB_2027[36:40] == b"FOR8MayaFOR8HEAD"
    assert MB_2027[40:64] == b"VERS" + bytes(4) + struct.pack(">Q", 4) + b"2027" + bytes(4)


@pytest.mark.parametrize("huge", [0xFFFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 2**40])
def test_huge_lengths_do_not_raise(parse, huge):
    vers = b"VERS" + bytes(4) + struct.pack(">Q", huge) + b"2027"
    assert parse(mb8(vers)) in ("2027", None)
    assert parse(mb8(b"UVER" + bytes(4) + struct.pack(">Q", huge) + b"undef", chunk8(b"VERS", b"2027"))) is None
    assert parse(form4(b"Maya", form4(b"HEAD", b"VERS" + struct.pack(">I", huge & 0xFFFFFFFF) + b"2012"))) in (
        "2012", None)


@pytest.mark.parametrize("vector", [MB_2027, MB_2027_WALK, MB_2012])
def test_every_truncation_is_safe(parse, vector):
    for n in range(len(vector) + 1):
        assert parse(vector[:n]) in (None, "2027", "2012", "202", "20", "2", "201"), n


def test_random_flips_never_raise(parse):
    rng = random.Random(28)
    for i in range(300):
        data = bytearray(rng.choice([MB_2027, MB_2027_WALK, MB_2012, MA_2027]))
        for _ in range(rng.randint(1, 6)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        assert isinstance(r := parse(bytes(data), "ma" if data.startswith(b"//") else "mb"), str | None), (i, r)
    for i in range(50):
        assert parse(rng.randbytes(rng.randrange(1, 5000))) is None, i


def test_indexing_stores_maya_version(daemon, lib):
    (lib / "cube.mb").write_bytes(MB_2027)
    (lib / "cube.ma").write_bytes(MA_2027)
    (lib / "old.mb").write_bytes(MB_2012)
    (lib / "junk.mb").write_bytes(b"not maya")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    assert {k: (v["dcc_version"], v["status"], v["has_thumbnail"], v["has_proxy"]) for k, v in got.items()} == {
        "cube.mb": ("2027", "unsupported", False, False), "cube.ma": ("2027", "unsupported", False, False),
        "old.mb": ("2012", "unsupported", False, False), "junk.mb": (None, "unsupported", False, False),
    }


@pytest.mark.maya
@pytest.mark.skipif(not MAYAPY, reason="no mayapy.exe under C:/Program Files/Autodesk/Maya*")
def test_real_maya_files_parse_as_detected_version(tmp_path):
    mayapy = Path(MAYAPY[-1])
    out = tmp_path / "out"
    out.mkdir()
    code = ("import maya.standalone as s; s.initialize(); import maya.cmds as c; c.polyCube(); "
            f"c.file(rename=r'{out.as_posix()}/cube.mb'); c.file(save=True, type='mayaBinary'); "
            f"c.file(rename=r'{out.as_posix()}/cube.ma'); c.file(save=True, type='mayaAscii'); s.uninitialize()")
    # A fresh MAYA_APP_DIR keeps mayapy away from the user's real Maya prefs.
    env = {**os.environ, "MAYA_APP_DIR": str(tmp_path / "maya_app"), "MAYA_DISABLE_CIP": "1",
           "MAYA_DISABLE_CER": "1", "MAYA_DISABLE_CLIC_IPM": "1"}
    subprocess.run([str(mayapy), "-c", code], env=env, check=True, timeout=300, capture_output=True)
    apps = [a for a in dcc.detect_apps() if a["id"].startswith("maya-")
            and Path(a["executable"]).parent == mayapy.parent]
    assert len(apps) == 1 and apps[0]["exts"] == ["ma", "mb"], apps
    assert (out / "cube.mb").read_bytes()[:4] == b"FOR8"
    assert parse_maya_header(str(out / "cube.mb")) == apps[0]["version"]
    assert parse_maya_header(str(out / "cube.ma")) == apps[0]["version"]
