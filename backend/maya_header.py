import re
import struct
import sys

MAX_READ = 4096
MAX_CHUNKS = 16
REQUIRES = re.compile(rb'^requires maya "([^"\r\n]{1,32})";', re.MULTILINE)
FIRST_LINE = re.compile(rb"//Maya ASCII (\S{1,32}) scene")
# Keep the leading numeric part: "2018ff09" -> "2018", "2016R2" -> "2016", "8.5" -> "8.5".
VERSION = re.compile(r"\d{1,4}(?:\.\d{1,2})?")


def _clean(raw: bytes) -> str | None:
    m = VERSION.match(raw.decode("ascii", "replace"))
    return m[0] if m else None


def _ascii(head: bytes) -> str | None:
    if m := REQUIRES.search(head) or FIRST_LINE.match(head):
        return _clean(m[1])
    return None


def _binary(head: bytes) -> str | None:
    if head[:4] == b"FOR8" and head[16:20] == b"Maya":
        form, pad, word, pos = b"FOR8", 4, 8, 20
    elif head[:4] == b"FOR4" and head[8:12] == b"Maya":
        form, pad, word, pos = b"FOR4", 0, 4, 12
    else:
        return None
    size_fmt = ">Q" if word == 8 else ">I"
    if head[pos:pos + 4] != form or head[pos + 4 + pad + word:pos + 8 + pad + word] != b"HEAD":
        return None
    pos += 8 + pad + word
    for _ in range(MAX_CHUNKS):
        start = pos + 4 + pad + word
        if start > len(head):
            return None
        tag = head[pos:pos + 4]
        (length,) = struct.unpack(size_fmt, head[pos + 4 + pad:start])
        if tag == b"VERS":
            return _clean(head[start:start + min(length, 32)].rstrip(b"\0")) if length else None
        pos = start + -(-length // word) * word
    return None


def parse_maya_header(path: str) -> str | None:
    with open(path, "rb") as f:
        head = f.read(MAX_READ)
    return _ascii(head) if head.startswith(b"//Maya ASCII") else _binary(head)


if __name__ == "__main__":
    print(parse_maya_header(sys.argv[1]))
