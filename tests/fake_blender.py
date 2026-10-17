import argparse
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "blender_export_proxy.py")
assert sys.argv[1:8] == ["-b", "--factory-startup", "--python-exit-code", "1", "-P", SCRIPT, "--"], sys.argv
rest = sys.argv[8:]
assert rest[0:6:2] == ["--input", "--out-png", "--out-meta"] and rest[6::2] in ([], ["--out-glb"]), sys.argv
p = argparse.ArgumentParser(allow_abbrev=False)
p.add_argument("--input", required=True)
p.add_argument("--out-png", required=True)
p.add_argument("--out-meta", required=True)
p.add_argument("--out-glb")
args = p.parse_args(rest)
assert all(os.path.isabs(v) for v in vars(args).values() if v), sys.argv
assert os.path.isfile(args.input), args.input
if log := os.environ.get("FAKE_BLENDER_LOG"):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{args.input}\t{'full' if args.out_glb else 'thumb'}\n")
time.sleep(float(os.environ.get("FAKE_BLENDER_SLEEP", "0")))


def dump_meta(**kw):
    meta = {"ok": True, "blender_version": "5.2.2", "poly_count": 1234,
            "proxy_poly_count": 617 if args.out_glb else None,
            "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}, "thumb_size": 512, "error": None} | kw
    with open(args.out_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f)


if os.environ.get("FAKE_BLENDER_FAIL"):
    dump_meta(ok=False, poly_count=None, proxy_poly_count=None, bounds=None, error="fake failure")
    sys.exit(1)
if args.out_glb:
    js = json.dumps({"asset": {"version": "2.0"}}).encode()
    js += b" " * (-len(js) % 4)
    with open(args.out_glb, "wb") as f:
        f.write(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<I", len(js)) + b"JSON" + js)


def chunk(t: bytes, d: bytes) -> bytes:
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))


with open(args.out_png, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))
dump_meta()
