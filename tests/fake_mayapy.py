import json
import os
import sys
import time
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "maya_export_fbx.py")
assert sys.argv[1] == SCRIPT and sys.argv[2:7:2] == ["--input", "--out-fbx", "--out-meta"], sys.argv
assert len(sys.argv) == 8, sys.argv
src, fbx, meta = sys.argv[3:8:2]
assert all(os.path.isabs(p) for p in (src, fbx, meta)) and os.path.isfile(src), sys.argv
assert fbx.endswith(".tmp.fbx") and Path(fbx).parent.name == ".cache", fbx
app_dir = os.environ.get("MAYA_APP_DIR", "")
assert app_dir == os.environ["FAKE_MAYA_APP_DIR"] and os.path.isdir(app_dir), app_dir
entry = None
if log := os.environ.get("FAKE_MAYA_LOG"):
    os.makedirs(log, exist_ok=True)
    entry = os.path.join(log, f"{time.time_ns()}-{os.getpid()}")
    Path(f"{entry}.txt").write_text(f"{src}\t{time.time_ns()}", encoding="utf-8")
time.sleep(float(os.environ.get("FAKE_MAYA_SLEEP", "0")))
Path(fbx).write_bytes(b"Kaydara FBX Binary  \x00")
fail = bool(os.environ.get("FAKE_MAYA_FAIL"))
Path(meta).write_text(json.dumps({"ok": not fail, "maya_version": "2027", "poly_count": None if fail else 12,
                                  "error": "RuntimeError: scene has no visible meshes" if fail else None}))
if entry:
    Path(f"{entry}.end").write_text(str(time.time_ns()), encoding="utf-8")
sys.exit(1 if fail else 0)
