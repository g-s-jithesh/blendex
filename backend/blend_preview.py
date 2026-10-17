import gzip
import struct
import sys
import zlib
from typing import BinaryIO

from .blend_header import LEGACY, V1

MAX_BYTES = 2 << 20
MAX_DIM = 512
V1_BHEAD = struct.Struct("<4siQqq")
SMALL_BHEAD8 = struct.Struct("<4siQii")
BHEAD4 = struct.Struct("<4siIii")


def _scan(f: BinaryIO) -> tuple[int, int, bytes] | None:
    head = f.read(12)
    if head[7:9] == b"17":
        head += f.read(5)
    if m := V1.fullmatch(head):
        if m[1] != b"17" or m[2] != b"01" or m[3] != b"v":
            return None
        bhead, len_field = V1_BHEAD, 3
    elif m := LEGACY.fullmatch(head):
        if m[2] != b"v" or int(m[3]) < 250:
            return None
        bhead, len_field = (SMALL_BHEAD8 if m[1] == b"-" else BHEAD4), 1
    else:
        return None
    used = len(head)
    while True:
        raw = f.read(bhead.size)
        if len(raw) != bhead.size:
            return None
        fields = bhead.unpack(raw)
        code, length = fields[0], fields[len_field]
        used += bhead.size + length
        if length < 0 or used > MAX_BYTES:
            return None
        data = f.read(length)
        if len(data) != length:
            return None
        if code == b"TEST":
            break
        if code != b"REND":
            return None
    if length < 8:
        return None
    w, h = struct.unpack_from("<ii", data)
    if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM) or length != 8 + w * h * 4:
        return None
    stride = w * 4
    px = bytearray().join(data[8 + y * stride : 8 + (y + 1) * stride] for y in reversed(range(h)))
    for i in range(3, len(px), 4):
        a = px[i]
        if 0 < a < 255:
            for c in range(i - 3, i):
                px[c] = min(255, (px[c] * 255 + a // 2) // a)
    return w, h, bytes(px)


def read_embedded_preview(path: str) -> tuple[int, int, bytes] | None:
    try:
        with open(path, "rb") as raw:
            magic = raw.read(4)
            raw.seek(0)
            if magic == b"\x28\xb5\x2f\xfd":
                from compression import zstd
                return _scan(zstd.ZstdFile(raw))
            if magic[:2] == b"\x1f\x8b":
                return _scan(gzip.GzipFile(fileobj=raw))
            return _scan(raw)
    except Exception:  # noqa: BLE001
        return None


def rgba_to_png(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    stride = w * 4
    scanlines = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    if not (preview := read_embedded_preview(sys.argv[1])):
        sys.exit(1)
    with open(sys.argv[2], "wb") as out:
        out.write(rgba_to_png(*preview))
    print(f"{preview[0]}x{preview[1]}")
