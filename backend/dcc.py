import ctypes
import glob
import logging
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import db
from .indexer import EXTS

try:
    import winreg
except ImportError:
    winreg = None

SYSTEM32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
OPEN_IMPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blender_open_import.py"
BLENDER_IMPORTS = ["obj", "fbx", "glb", "gltf", "stl", "ply", "usd", "usda", "usdc", "usdz", "abc"]
CANDIDATES = {
    "win32": [],
    "darwin": ["/Applications/Blender*.app/Contents/MacOS/Blender", "/Applications/Autodesk/maya*/Maya.app/Contents/MacOS/Maya"],
    "linux": ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender", "/opt/blender*/blender",
              str(Path.home() / "blender*" / "blender"), "/usr/autodesk/maya*/bin/maya"],
}
UNINSTALL = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
MAYA_KEY = r"SOFTWARE\Autodesk\Maya"
FILE_EXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.blend"
BLEND_PROGIDS = (r"Applications\blender.exe", r"Applications\blender-launcher.exe")
log = logging.getLogger(__name__)


def _reg(root: int, key: str, name: str = "") -> str:
    try:
        with winreg.OpenKey(root, key) as k:
            value = winreg.QueryValueEx(k, name)[0]
    except OSError:
        return ""
    return os.path.expandvars(value) if isinstance(value, str) else ""


def _subkeys(root: int, key: str) -> list[str]:
    try:
        with winreg.OpenKey(root, key) as k:
            return [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except OSError:
        return []


def _value_names(root: int, key: str) -> list[str]:
    try:
        with winreg.OpenKey(root, key) as k:
            return [winreg.EnumValue(k, i)[0] for i in range(winreg.QueryInfoKey(k)[1])]
    except OSError:
        return []


def exes_in(folder: Path) -> list[Path]:
    suffix = ".exe" if sys.platform == "win32" else ""
    return [folder / f"blender{suffix}", folder / f"maya{suffix}", folder / "bin" / f"maya{suffix}"]


def _near(value: str) -> list[str]:
    m = re.match(r'(?i)\s*"?([^"]*?\.exe|[^",]+)', value)
    if not m or not m[1].strip():
        return []
    p = Path(m[1].strip())
    folder = p.parent if p.suffix.lower() == ".exe" else p
    return [glob.escape(str(c)) for c in exes_in(folder)]


def _fixed_drives() -> list[str]:
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    return [f"{chr(65 + i)}:/" for i in range(26) if mask >> i & 1 and k32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]


def _program_files() -> list[str]:
    return [f"{d}{pf}/{sub}" for d in _fixed_drives() for pf in ("Program Files", "Program Files (x86)")
            for sub in ("Blender Foundation/*/blender.exe", "Blender*/blender.exe", "Autodesk/Maya*/bin/maya.exe")]


def _steam() -> list[str]:
    root = _reg(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    if not root:
        return []
    with open(Path(root) / "steamapps" / "libraryfolders.vdf", encoding="utf-8", errors="replace") as f:
        vdf = f.read(1 << 20)
    libs = [root, *(p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf))]
    return [glob.escape(str(Path(lib) / "steamapps" / "common" / "Blender" / "blender.exe")) for lib in libs]


def _association() -> list[str]:
    hkcr, hkcu = winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER
    progids = [_reg(hkcr, ".blend"), _reg(hkcu, r"Software\Classes\.blend"), _reg(hkcu, FILE_EXTS + r"\UserChoice",
               "ProgId"), *_value_names(hkcr, r".blend\OpenWithProgids"), *BLEND_PROGIDS]
    cmds = [_reg(root, rf"{base}{prog}\shell\open\command") for prog in dict.fromkeys(filter(None, progids))
            for root, base in ((hkcu, "Software\\Classes\\"), (hkcr, ""))]
    return [p for cmd in cmds for p in _near(cmd)]


def _uninstall() -> list[str]:
    out = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in UNINSTALL:
            for key in (rf"{base}\{sub}" for sub in _subkeys(root, base)):
                if _reg(root, key, "DisplayName").startswith(("Blender", "Autodesk Maya")):
                    out += [p for name in ("InstallLocation", "DisplayIcon") for p in _near(_reg(root, key, name))]
    return out


def _maya_installs() -> list[tuple[str, str]]:
    hklm = winreg.HKEY_LOCAL_MACHINE
    return [(ver, loc) for ver in _subkeys(hklm, MAYA_KEY)
            if (loc := _reg(hklm, rf"{MAYA_KEY}\{ver}\Setup\InstallPath", "MAYA_INSTALL_LOCATION"))]


def _maya_registry() -> list[str]:
    return [p for _, loc in _maya_installs() for p in _near(loc)]


def _windows_apps() -> list[str]:
    local = os.environ.get("LOCALAPPDATA")
    return [glob.escape(str(Path(local) / "Microsoft" / "WindowsApps")) + "/blender*.exe"] if local else []


def _scoop() -> list[str]:
    return [glob.escape(str(Path.home() / "scoop" / "apps" / "blender" / "current" / "blender.exe"))]


def windows_sources() -> list[tuple[str, Callable[[], list[str]]]]:
    return [("Program Files", _program_files), ("Steam", _steam), (".blend association", _association),
            ("uninstall entries", _uninstall), ("Maya registry", _maya_registry), ("WindowsApps", _windows_apps),
            ("Scoop", _scoop)]


def cli_version(exe: Path) -> str | None:
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        out = subprocess.run([str(exe), "--version"], check=False, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", timeout=10, **kw).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return m[1] if (m := re.search(r"Blender (\d+\.\d+)", out or "")) else None


def _version(exe: Path, maya: bool) -> str | None:
    if m := re.search(r"(?i)maya(\d{4})" if maya else r"(?i)blender[ -]?(\d+\.\d+)", exe.as_posix()):
        return m[1]
    plist = exe.parent.parent / "Info.plist"
    try:
        full = plistlib.loads(plist.read_bytes()).get("CFBundleShortVersionString", "")
        return ".".join(full.split(".")[:1 if maya else 2]) or None
    except (OSError, plistlib.InvalidFileException):
        pass
    if not maya:
        return cli_version(exe)
    if winreg and sys.platform == "win32":
        return next((v for v, loc in _maya_installs() if exe.is_relative_to(Path(loc).resolve())), None)
    return None


def normalize(p: str) -> Path:
    exe = Path(p).resolve()
    # /snap/bin/blender is a symlink to /usr/bin/snap, which dispatches on the name it was called by.
    return exe if exe.stem.lower().startswith(("blender", "maya")) else Path(p).absolute()


def _sources(custom: list[str]) -> list[tuple[str, list[str]]]:
    sources = [("custom", [glob.escape(c) for c in custom]),
               ("standard dirs", CANDIDATES.get(sys.platform, CANDIDATES["linux"])),
               ("PATH", [glob.escape(p) for p in (shutil.which("blender"), shutil.which("maya")) if p])]
    for name, find in windows_sources() if sys.platform == "win32" else []:
        try:
            sources.append((name, find()))
        except Exception as e:  # noqa: BLE001
            log.warning("app source %s failed: %s: %s", name, type(e).__name__, e)
    return sources


def detect_apps(custom: list[str] | None = None) -> list[dict]:
    apps: dict[str, dict] = {}
    seen: set[str] = set()
    for source, patterns in _sources(custom or []):
        for p in (p for pattern in patterns for p in sorted(glob.glob(pattern))):
            exe = normalize(p)
            if not exe.is_file() or exe.as_posix().lower() in seen:
                continue
            seen.add(exe.as_posix().lower())
            maya = "maya" in exe.name.lower()
            name = "Maya" if maya else "Blender"
            ver = _version(exe, maya)
            app_id = f"{name.lower()}-{ver}" if ver else name.lower()
            log.info("found %s at %s (source: %s)", app_id, exe.as_posix(), source)
            apps.setdefault(app_id, {
                "id": app_id, "name": f"{name} {ver}" if ver else name, "version": ver,
                "executable": exe.as_posix(), "exts": ["ma", "mb"] if maya else ["blend", *BLENDER_IMPORTS],
            })
    if not any(i.startswith("blender") for i in apps):
        log.warning("no Blender found")
    if sys.platform == "win32":
        for app_id, name, exe in (("explorer", "Explorer", SYSTEM32.parent / "explorer.exe"),
                                  ("open-with", "other app…", SYSTEM32 / "rundll32.exe")):
            apps[app_id] = {"id": app_id, "name": name, "version": None, "executable": exe.as_posix(),
                            "exts": sorted(EXTS)}
    elif sys.platform != "darwin":
        xdg = shutil.which("xdg-open")
        for app_id, name, exe in (("file-manager", "file manager", shutil.which("dbus-send") or xdg),
                                  ("default-app", "default app", xdg)):
            if exe:
                apps[app_id] = {"id": app_id, "name": name, "version": None, "executable": exe, "exts": sorted(EXTS)}
    return list(apps.values())


def custom_executable(raw: str) -> Path:
    p = Path(raw.strip().strip('"')).expanduser()
    if p.is_dir():
        p = next((c for c in exes_in(p) if c.is_file()), p)
    if p.name.lower() == "blender-launcher.exe":
        p = exes_in(p.parent)[0]
    if not p.is_absolute() or not p.is_file():
        raise ValueError(f"no Blender or Maya executable at {raw}; pick blender.exe or maya.exe, or its folder")
    exe = normalize(str(p))
    name = exe.name.lower()
    named = name.startswith("blender") or exe.stem.lower() == "maya"
    if not named or (sys.platform == "win32" and not name.endswith(".exe")):
        raise ValueError(f"{exe.name} is not Blender or Maya; pick blender.exe or maya.exe")
    if name.startswith("blender") and not cli_version(exe):
        raise ValueError(f"{exe} did not report a Blender version; pick blender.exe from a Blender 4.2+ install")
    return exe


def _version_key(app: dict) -> tuple[int, ...]:
    nums = re.findall(r"\d+", app.get("version") or "0")
    return tuple(int(x) for x in nums) if nums else (0,)


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
        return f'"{Path(exe)}" shell32.dll,OpenAs_RunDLL "{native}"'
    if app["id"] == "file-manager":
        if dbus := shutil.which("dbus-send"):
            return [dbus, "--session", "--print-reply", "--reply-timeout=5000", "--dest=org.freedesktop.FileManager1",
                    "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{Path(path).as_uri()}", "string:"]
        return [shutil.which("xdg-open") or "xdg-open", str(Path(path).parent)]
    if app["id"].startswith("blender") and Path(path).suffix.lower() != ".blend":
        return [exe, "--python", str(OPEN_IMPORT_SCRIPT), "--", native]
    return [exe, native]


def _spawn(argv: list[str] | str) -> None:
    if os.name == "nt":
        kw = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        # Lets the app (or the COM-started "Open with" picker) take the foreground instead of opening minimized.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    else:
        kw = {"start_new_session": True}
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **kw)


def _show_items(argv: list[str], folder: str) -> None:
    try:
        ok = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok and (xdg := shutil.which("xdg-open")):
        _spawn([xdg, folder])


def launch(app: dict, path: str) -> None:
    argv = launch_command(app, path)
    # dbus-send exits 0 without --print-reply even when no file manager implements FileManager1.
    if app["id"] == "file-manager" and "--print-reply" in argv:
        threading.Thread(target=_show_items, args=(argv, str(Path(path).parent)), daemon=True).start()
    else:
        _spawn(argv)
