import re
import sys
import zlib

MAX_READ = 256 * 1024
LEGACY = re.compile(rb"BLENDER([_-])([vV])(\d{3})")
V1 = re.compile(rb"BLENDER(\d\d)-(\d\d)([vV])(\d{4})")


def _inflate(data: bytes, kind: str) -> bytes:
    if kind == "gzip":
        return zlib.decompressobj(wbits=31).decompress(data, 64)
    from compression import zstd
    return zstd.ZstdDecompressor().decompress(data, max_length=64)


def parse_blend_header(path: str) -> dict | None:
    with open(path, "rb") as f:
        head = f.read(MAX_READ)
    compressed = None
    if head[:4] == b"\x28\xb5\x2f\xfd":
        compressed = "zstd"
    elif head[:2] == b"\x1f\x8b":
        compressed = "gzip"
    if compressed:
        try:
            head = _inflate(head, compressed)
        except ImportError:
            return {"version": None, "pointer_size": None, "endian": None, "compressed": "zstd", "header_format": None}
        except Exception:  # noqa: BLE001
            return None
    if m := V1.match(head):
        if m[1] != b"17":
            return None
        fmt, ptr, endian, ver = "v1", 8, m[3], int(m[4])
    elif m := LEGACY.match(head):
        fmt, ptr, endian, ver = "legacy", 8 if m[1] == b"-" else 4, m[2], int(m[3])
    else:
        return None
    return {
        "version": f"{ver // 100}.{ver % 100}",
        "pointer_size": ptr,
        "endian": "little" if endian == b"v" else "big",
        "compressed": compressed,
        "header_format": fmt,
    }


if __name__ == "__main__":
    info = parse_blend_header(sys.argv[1])
    print(info["version"] if info else None)
