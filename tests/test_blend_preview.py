import gzip
import random
import struct
import subprocess
import sys

import pytest
from compression import zstd
from conftest import (
    BHEADS,
    EXPECTED_2X2,
    PIXELS_2X2,
    ROOT,
    block,
    decode_png,
    preview_blend,
    thumb_payload,
)

from backend import blend_preview
from backend.blend_preview import MAX_BYTES, read_embedded_preview, rgba_to_png

SEEK_TABLE = struct.pack("<II", 0x184D2A5E, 9) + struct.pack("<IB", 0, 0) + bytes.fromhex("B1EA928F")


def zstd_split(data: bytes, at: int) -> bytes:
    return zstd.compress(data[:at]) + zstd.compress(data[at:]) + SEEK_TABLE


def test_split_point_is_inside_test_block():
    data = preview_blend("v1")
    at = data.index(b"TEST") + 32 + 10
    assert data.index(b"TEST") < at < data.index(b"GLOB")


def straight(rows_bottom_up: list[bytes]) -> bytes:
    out = bytearray(b"".join(reversed(rows_bottom_up)))
    for i in range(0, len(out), 4):
        a = out[i + 3]
        if 0 < a < 255:
            for c in range(i, i + 3):
                out[c] = min(255, (out[c] * 255 + a // 2) // a)
    return bytes(out)


@pytest.fixture
def read(tmp_path):
    def run(data: bytes):
        f = tmp_path / "x.blend"
        f.write_bytes(data)
        return read_embedded_preview(str(f))

    return run


WRAP = {
    "plain": lambda d: d,
    "gzip": gzip.compress,
    "zstd_split": lambda d: zstd_split(d, d.index(b"TEST") + 32 + 10),
}


@pytest.mark.parametrize("wrap", list(WRAP))
@pytest.mark.parametrize("kind", list(BHEADS))
def test_spec_vector_flip_and_unpremultiply(read, kind, wrap):
    assert read(WRAP[wrap](preview_blend(kind))) == (2, 2, EXPECTED_2X2)


@pytest.mark.parametrize("kind", list(BHEADS))
def test_single_rend_and_realistic_size(read, kind):
    rng = random.Random(7)
    w, h = 128, 77
    rows = []
    for _ in range(h):
        row = bytearray()
        for _ in range(w):
            a = rng.choice([0, 255, rng.randrange(1, 255)])
            row += bytes(rng.randrange(0, a + 1) for _ in range(3)) + bytes([a])
        rows.append(bytes(row))
    data = BHEADS[kind][0] + block(kind, b"REND", bytes(72)) + block(kind, b"TEST", thumb_payload(w, h, b"".join(rows)))
    assert read(data + block(kind, b"GLOB", bytes(8))) == (w, h, straight(rows))
    assert read(zstd.compress(data)) == (w, h, straight(rows))


def test_non_square_orientation_exact_bytes(read):
    bottom = bytes([1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255])
    top = bytes([101, 102, 103, 255, 104, 105, 106, 255, 107, 108, 109, 255])
    assert read(preview_blend("v1", thumb_payload(3, 2, bottom + top))) == (3, 2, top + bottom)


def test_unpremultiply_rules(read):
    px = bytes([10, 20, 30, 0, 1, 2, 3, 255, 1, 1, 1, 1, 50, 25, 0, 100, 64, 64, 64, 128, 200, 9, 0, 100])
    want = bytes([10, 20, 30, 0, 1, 2, 3, 255, 255, 255, 255, 1, 128, 64, 0, 100, 128, 128, 128, 128, 255, 23, 0, 100])
    assert read(preview_blend("v1", thumb_payload(6, 1, px))) == (6, 1, want)


@pytest.mark.parametrize(("w", "h"), [(1, 1), (512, 1), (1, 512), (512, 512)])
def test_dimension_limits_accepted(read, w, h):
    got = read(preview_blend("legacy8", thumb_payload(w, h, bytes(w * h * 4))))
    assert got == (w, h, bytes(w * h * 4))


def _bad_test(w, h, extra=0):
    return thumb_payload(w, h, bytes(max(0, abs(w * h) * 4 + extra)))


V1 = BHEADS["v1"][0]
REND = block("v1", b"REND", bytes(264))
TEST = block("v1", b"TEST", thumb_payload(2, 2))
GLOB = block("v1", b"GLOB", bytes(16))
MALFORMED = {
    "big_endian_legacy": preview_blend("legacy8", head=b"BLENDER-V404"),
    "big_endian_v1": preview_blend("v1", head=b"BLENDER17-01V0502"),
    "version_249": preview_blend("legacy8", head=b"BLENDER-v249"),
    "v1_format_02": preview_blend("v1", head=b"BLENDER17-02v0502"),
    "v1_size_99": preview_blend("v1", head=b"BLENDER99-01v0502"),
    "no_test": V1 + REND + GLOB,
    "glob_before_test": V1 + REND + GLOB + TEST,
    "width_0": preview_blend("v1", _bad_test(0, 2)),
    "height_0": preview_blend("v1", _bad_test(2, 0)),
    "negative_width": preview_blend("v1", _bad_test(-2, 2)),
    "width_513": preview_blend("v1", _bad_test(513, 1)),
    "height_513": preview_blend("v1", _bad_test(1, 513)),
    "payload_one_too_long": preview_blend("v1", _bad_test(2, 2, 1)),
    "payload_one_too_short": preview_blend("v1", _bad_test(2, 2, -1)),
    "payload_4_bytes": preview_blend("v1", b"\x02\x00\x00\x00"),
    "payload_empty": preview_blend("v1", b""),
    "cut_inside_test_data": (V1 + REND + TEST)[:-5],
    "cut_inside_bhead": (V1 + REND)[:-264 - 3],
    "len_minus_8": V1 + block("v1", b"REND", b"", length=-8) + TEST,
    "rend_len_2_pow_40": V1 + block("v1", b"REND", bytes(64), length=1 << 40) + TEST,
    "legacy4_len_negative": BHEADS["legacy4"][0] + block("legacy4", b"REND", b"", length=-1),
    "header_only": V1,
    "empty": b"",
    "random": random.Random(3).randbytes(4096),
    "zstd_magic_garbage": bytes.fromhex("28B52FFD") + random.Random(4).randbytes(512),
    "gzip_magic_garbage": bytes.fromhex("1F8B0800") + random.Random(5).randbytes(512),
    "zstd_truncated": zstd.compress(preview_blend("v1"))[:40],
    "gzip_truncated": gzip.compress(preview_blend("v1"))[:40],
    "zstd_non_blend": zstd.compress(b"NOTBLEND" * 100),
    "gzip_non_blend": gzip.compress(b"NOTBLEND" * 100),
    "zstd_of_no_test": zstd.compress(V1 + REND + GLOB),
}


@pytest.mark.parametrize("name", list(MALFORMED))
def test_malformed_returns_none(read, name):
    assert read(MALFORMED[name]) is None


def test_missing_path_and_directory_return_none(tmp_path):
    assert read_embedded_preview(str(tmp_path / "missing.blend")) is None
    assert read_embedded_preview(str(tmp_path)) is None


def test_zstd_without_decoder_returns_none(read, monkeypatch):
    import compression

    monkeypatch.delattr(compression, "zstd")
    monkeypatch.setitem(sys.modules, "compression.zstd", None)
    assert read(zstd.compress(preview_blend("v1"))) is None
    assert read(gzip.compress(preview_blend("v1"))) == (2, 2, EXPECTED_2X2)


def _with_rend(rend_len: int) -> bytes:
    return V1 + block("v1", b"REND", bytes(rend_len)) + TEST + GLOB


def test_two_mib_bound_is_inclusive(read):
    exact = MAX_BYTES - len(V1) - 32 - len(TEST)
    assert MAX_BYTES == 2 * 1024 * 1024
    assert read(_with_rend(exact)) == (2, 2, EXPECTED_2X2)
    assert read(_with_rend(exact + 1)) is None
    assert read(zstd.compress(_with_rend(exact + 1))) is None
    assert read(gzip.compress(_with_rend(exact + 1))) is None


@pytest.mark.parametrize("wrap", ["plain", "gzip", "zstd"])
def test_decompressed_bytes_consumed_never_exceed_bound(tmp_path, monkeypatch, wrap):
    reads: list[int] = []
    real_scan = blend_preview._scan

    class Counting:
        def __init__(self, f):
            self.f = f

        def read(self, n):
            assert 0 <= n <= MAX_BYTES, n
            data = self.f.read(n)
            reads.append(len(data))
            return data

    monkeypatch.setattr(blend_preview, "_scan", lambda f: real_scan(Counting(f)))
    compress = {"plain": lambda d: d, "gzip": gzip.compress, "zstd": zstd.compress}[wrap]
    cases = [
        (_with_rend(MAX_BYTES), 1024),
        (V1 + block("v1", b"REND", bytes(1 << 20)) * 3 + TEST, (1 << 20) + 1024),
        (V1 + block("v1", b"REND", bytes(64), length=1 << 40) + bytes(1 << 20), 1024),
        (V1 + REND + block("v1", b"GLOB", bytes(3 << 20)) + TEST, 1024),
        (V1 + REND + GLOB + bytes(4 << 20), 1024),
    ]
    for i, (data, limit) in enumerate(cases):
        f = tmp_path / f"{i}.blend"
        f.write_bytes(compress(data))
        reads.clear()
        assert read_embedded_preview(str(f)) is None, i
        assert sum(reads) <= limit, (i, sum(reads))


def test_png_is_valid_and_decodes_to_rows():
    png = rgba_to_png(2, 2, EXPECTED_2X2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    assert struct.unpack(">II", png[16:24]) == (2, 2)
    assert decode_png(png) == (2, 2, EXPECTED_2X2)
    rng = random.Random(9)
    px = rng.randbytes(128 * 70 * 4)
    assert decode_png(rgba_to_png(128, 70, px)) == (128, 70, px)


def test_cli_writes_png_and_exits_1_without_preview(tmp_path):
    src, none, out = tmp_path / "p.blend", tmp_path / "n.blend", tmp_path / "o.png"
    src.write_bytes(WRAP["zstd_split"](preview_blend("v1")))
    none.write_bytes(MALFORMED["no_test"])
    run = [sys.executable, "-m", "backend.blend_preview"]
    r = subprocess.run([*run, str(src), str(out)], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert (r.returncode, r.stdout.strip()) == (0, "2x2"), r.stderr
    assert decode_png(out.read_bytes()) == (2, 2, EXPECTED_2X2)
    r = subprocess.run([*run, str(none), str(tmp_path / "x.png")], cwd=ROOT, capture_output=True, timeout=60,
                       check=False)
    assert r.returncode == 1 and not (tmp_path / "x.png").exists()


def test_fuzz_never_raises(tmp_path):
    rng = random.Random(42)
    seeds = [preview_blend(k) for k in BHEADS] + [PIXELS_2X2]
    f = tmp_path / "f.blend"
    for i in range(1500):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randrange(1, 6)):
            pos = rng.randrange(len(data))
            data[pos:pos + rng.randrange(1, 5)] = rng.randbytes(rng.randrange(0, 5))
        data = bytes(data[: rng.randrange(len(data) + 1)] if i % 4 == 0 else data)
        f.write_bytes(rng.choice([lambda d: d, gzip.compress, zstd.compress])(data))
        got = read_embedded_preview(str(f))
        assert got is None or (len(got) == 3 and len(got[2]) == got[0] * got[1] * 4), data
