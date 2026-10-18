import glob
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import db
from .indexer import EXTS

PROGRAM_FILES = os.environ.get("ProgramFiles", "C:/Program Files")
SYSTEM32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
OPEN_IMPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blender_open_import.py"
BLENDER_IMPORTS = ["obj", "fbx", "glb", "gltf", "stl", "ply", "usd", "usda", "usdc", "usdz", "abc"]
CANDIDATES = {
    "win32": [f"{PROGRAM_FILES}/Blender Foundation/Blender */blender.exe", f"{PROGRAM_FILES}/Autodesk/Maya*/bin/maya.exe"],
    "darwin": ["/Applications/Blender*.app/Contents/MacOS/Blender", "/Applications/Autodesk/maya*/Maya.app/Contents/MacOS/Maya"],
    "linux": ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender", "/opt/blender*/blender",
              "/usr/autodesk/maya*/bin/maya"],
}


def _version(exe: Path, maya: bool) -> str | None:
    if m := re.search(r"(?i)maya(\d{4})" if maya else r"(?i)blender[ -]?(\d+\.\d+)", exe.as_posix()):
        return m[1]
    plist = exe.parent.parent / "Info.plist"
    try:
        full = plistlib.loads(plist.read_bytes()).get("CFBundleShortVersionString", "")
    except (OSError, plistlib.InvalidFileException):
        return None
    return ".".join(full.split(".")[:1 if maya else 2]) or None


def detect_apps() -> list[dict]:
    found = [p for pattern in CANDIDATES.get(sys.platform, CANDIDATES["linux"]) for p in sorted(glob.glob(pattern))]
    found += [p for p in (shutil.which("blender"), shutil.which("maya")) if p]
    apps: dict[str, dict] = {}
    seen: set[str] = set()
    for p in found:
        exe = Path(p).resolve()
        if not exe.is_file() or exe.as_posix().lower() in seen:
            continue
        seen.add(exe.as_posix().lower())
        maya = "maya" in exe.name.lower()
        name = "Maya" if maya else "Blender"
        ver = _version(exe, maya)
        app_id = f"{name.lower()}-{ver}" if ver else name.lower()
        apps.setdefault(app_id, {
            "id": app_id, "name": f"{name} {ver}" if ver else name, "version": ver,
            "executable": exe.as_posix(), "exts": ["ma", "mb"] if maya else ["blend", *BLENDER_IMPORTS],
        })
    if sys.platform == "win32":
        for app_id, name, exe in (("explorer", "Explorer", SYSTEM32.parent / "explorer.exe"),
                                  ("open-with", "other app…", SYSTEM32 / "rundll32.exe")):
            apps[app_id] = {"id": app_id, "name": name, "version": None, "executable": exe.as_posix(),
                            "exts": sorted(EXTS)}
    return list(apps.values())


def _version_key(app: dict) -> tuple[int, ...]:
    return tuple(int(x) for x in (app["version"] or "0").split("."))


def blender_command(conn: sqlite3.Connection) -> list[str] | None:
    exe = os.environ.get("DAM_BLENDER")
    if not exe:
        blenders = [a for a in db.list_apps(conn) if a["id"].startswith("blender")]
        exe = max(blenders, key=_version_key)["executable"] if blenders else None
    if not exe:
        return None
    return [sys.executable, exe] if exe.lower().endswith(".py") else [exe]


def launch_command(app: dict, path: str) -> list[str] | str:
    exe, native = app["executable"], str(Path(path))
    # explorer and rundll32 parse their raw command line themselves, so they get a string (still no shell).
    if app["id"] == "explorer":
        return f'"{Path(exe)}" /select,"{native}"'
    if app["id"] == "open-with":
        return f'"{Path(exe)}" shell32.dll,OpenAs_RunDLL {native}'
    if app["id"].startswith("blender") and Path(path).suffix.lower() != ".blend":
        return [exe, "--python", str(OPEN_IMPORT_SCRIPT), "--", native]
    return [exe, native]


def launch(app: dict, path: str) -> None:
    if os.name == "nt":
        kw = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        kw = {"start_new_session": True}
    subprocess.Popen(launch_command(app, path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)
