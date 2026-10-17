import gzip
import random
import sys

import pytest
from compression import zstd
from conftest import HEADERS, blend

from backend import blend_header
from backend.blend_header import parse_blend_header

EXPECTED = {
    "2.90": ("legacy", 8, "little"),
    "3.6": ("legacy", 8, "little"),
    "4.1": ("legacy", 8, "little"),
    "4.2": ("legacy", 8, "little"),
    "4.3": ("legacy", 8, "little"),
    "4.4": ("legacy", 8, "little"),
    "5.0": ("v1", 8, "little"),
}
COMPRESS = {None: lambda b: b, "gzip": gzip.compress, "zstd": zstd.compress}


@pytest.fixture
def parse(tmp_path):
    def run(data: bytes):
        f = tmp_path / "x.blend"
        f.write_bytes(data)
        return parse_blend_header(str(f))

    return run


def test_vector_table_matches_spec_expectations():
    assert set(HEADERS) == set(EXPECTED)


@pytest.mark.parametrize("compressed", list(COMPRESS))
@pytest.mark.parametrize("version", list(EXPECTED))
def test_spec_vectors(parse, version, compressed):
    fmt, ptr, endian = EXPECTED[version]
    assert parse(COMPRESS[compressed](blend(version))) == {
        "version": version, "pointer_size": ptr, "endian": endian, "compressed": compressed, "header_format": fmt,
    }


@pytest.mark.parametrize("compressed", list(COMPRESS))
@pytest.mark.parametrize(("raw", "expected"), [
    (b"BLENDER_v279", ("2.79", 4, "little", "legacy")),
    (b"BLENDER_V279", ("2.79", 4, "big", "legacy")),
    (b"BLENDER-V402", ("4.2", 8, "big", "legacy")),
    (b"BLENDER17-01V0500", ("5.0", 8, "big", "v1")),
    (b"BLENDER17-02v0501", ("5.1", 8, "little", "v1")),
    (b"BLENDER17-01v1203", ("12.3", 8, "little", "v1")),
])
def test_synthetic_pointer_and_endian_combos(parse, raw, expected, compressed):
    r = parse(COMPRESS[compressed](raw + b"REND" + bytes(28)))
    assert (r["version"], r["pointer_size"], r["endian"], r["header_format"]) == expected
    assert r["compressed"] == compressed


def test_large_file_with_header_is_parsed(parse):
    assert parse(blend("4.2", bytes(1024 * 1024)))["version"] == "4.2"
    assert parse(zstd.compress(blend("5.0", bytes(1024 * 1024))))["version"] == "5.0"
    assert parse(gzip.compress(blend("3.6", random.randbytes(512 * 1024))))["version"] == "3.6"


@pytest.mark.parametrize("data", [
    b"",
    b"NOTBLENDNOTBLEND" * 4,
    b"BLENDER-v4",
    b"BLENDER",
    b"BLENDER99-01v0500GLOB" + bytes(16),
    b"BLENDER17-01v050",
    b"BLENDER*v402GLOB",
    b"BLENDER-x402GLOB",
    b"BLENDER-vABCGLOB",
    b" BLENDER-v402",
    b"blender-v402",
    bytes.fromhex("28 B5 2F FD 00"),
    bytes.fromhex("1F 8B 08"),
    bytes.fromhex("1F 8B"),
    bytes.fromhex("28 B5 2F FD"),
], ids=repr)
def test_negative_vectors(parse, data):
    assert parse(data) is None


@pytest.mark.parametrize("kind", ["gzip", "zstd"])
@pytest.mark.parametrize("cut", [0.25, 0.5, 0.9])
def test_truncated_compressed_header(parse, kind, cut):
    data = COMPRESS[kind](blend("4.2"))
    assert parse(data[: max(5, int(len(data) * cut))]) in (None, parse(data))


@pytest.mark.parametrize("payload", [b"NOTBLEND" * 8, b"", b"BLENDER-v4"])
@pytest.mark.parametrize("kind", ["gzip", "zstd"])
def test_compressed_non_blend_payload(parse, kind, payload):
    assert parse(COMPRESS[kind](payload)) is None


@pytest.mark.parametrize("kind", ["gzip", "zstd"])
def test_corrupt_compressed_stream_returns_none(parse, kind):
    magic = bytes.fromhex("28 B5 2F FD") if kind == "zstd" else bytes.fromhex("1F 8B 08 00")
    assert parse(magic + random.Random(1).randbytes(200)) is None


def test_zstd_without_decoder_returns_partial_dict(parse, monkeypatch):
    import compression

    monkeypatch.delattr(compression, "zstd")
    monkeypatch.setitem(sys.modules, "compression.zstd", None)
    assert parse(zstd.compress(blend("4.2"))) == {
        "version": None, "pointer_size": None, "endian": None, "compressed": "zstd", "header_format": None,
    }
    assert parse(gzip.compress(blend("4.2")))["version"] == "4.2"


def test_read_is_bounded(parse, monkeypatch):
    sizes = []
    real_open = open

    class Spy:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def read(self, n=-1):
            sizes.append(n)
            return self.f.read(n)

        def __getattr__(self, name):
            raise AssertionError(f"parser used file.{name}")

    monkeypatch.setattr(blend_header, "open", lambda *a, **k: Spy(real_open(*a, **k)), raising=False)
    assert parse(blend("4.2", bytes(4 * 1024 * 1024)))["version"] == "4.2"
    assert sizes and all(0 <= n <= 256 * 1024 for n in sizes)


def test_fuzz_never_raises(parse):
    rng = random.Random(1234)
    prefixes = [b"", b"BLENDER", b"BLENDER-v", b"BLENDER17-01", bytes.fromhex("28B52FFD"), bytes.fromhex("1F8B08")]
    for i in range(1500):
        data = rng.choice(prefixes) + rng.randbytes(rng.randrange(0, 80))
        if i % 3 == 0:
            data = rng.choice([gzip.compress, zstd.compress])(data)
            data = data[: rng.randrange(0, len(data) + 1)]
        r = parse(data)
        assert r is None or isinstance(r, dict), data
