import os
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "release"
ISCC_PATHS = [Path(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
              Path(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe")]
# setuptools only comes in through cffi's compiler shim; clr_loader uses cffi without compiling.
UNUSED = ["pytest", "setuptools", "pkg_resources", "_distutils_hack", "pydoc", "pydoc_data", "_pyrepl",
          "httptools", "watchfiles", "dotenv", "yaml"]
LINUX = sys.platform == "linux"
# Qt WebEngine links these, so PyInstaller would bundle their modules, QML trees and plugins; packaging/hooks also drops
# the GTK theme and CUPS plugins, which would bundle the build machine's GTK and CUPS.
QT_ARGS = ["--additional-hooks-dir", ROOT / "packaging" / "hooks",
           *[arg for mod in ("PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets", "PyQt6.QtPositioning",
                             "PyQt6.QtDBus") for arg in ("--exclude-module", mod)]]
sys.path.insert(0, str(ROOT))

from backend.app import VERSION


def run(*cmd: str | Path) -> None:
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)


def iscc() -> str:
    found = shutil.which("iscc") or next((str(p) for p in ISCC_PATHS if p.is_file()), None)
    if not found:
        raise SystemExit("Inno Setup 6 not found; install it with: winget install JRSoftware.InnoSetup")
    return found


def package_linux(app: Path) -> Path:
    qt = app / "_internal" / "PyQt6" / "Qt6"
    for unused in [*(qt / "translations").glob("*.qm"), *(qt / "translations" / "qtwebengine_locales").glob("*.pak")]:
        if unused.name != "en-US.pak":
            unused.unlink()
    ico = (ROOT / "packaging" / "blendex.ico").read_bytes()
    size, offset = max(struct.unpack_from("<2I", ico, 14 + 16 * i) for i in range(int.from_bytes(ico[4:6], "little")))
    (app / "blendex.png").write_bytes(ico[offset:offset + size])
    shutil.copy(ROOT / "packaging" / "blendex.desktop", app)
    shutil.copy(ROOT / "packaging" / "install.sh", app)
    (app / "install.sh").chmod(0o755)
    tarball = OUT / f"Blendex-{VERSION}-linux-x86_64.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(app, arcname="Blendex")
    return tarball


def main() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found; install Node.js")
    compiler = None if LINUX else iscc()
    run(npm, "--prefix", "frontend", "ci")
    run(npm, "--prefix", "frontend", "run", "build")
    shutil.rmtree(OUT, ignore_errors=True)
    data = [(ROOT / "frontend" / "dist", "web"), (ROOT / "scripts", "scripts"),
            (ROOT / "backend" / "migrations", "backend/migrations")]
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Blendex",
        *(QT_ARGS if LINUX else ["--icon", ROOT / "packaging" / "blendex.ico"]),
        "--distpath", OUT / "dist", "--workpath", OUT / "build", "--specpath", OUT, "--paths", ROOT,
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        *[arg for src, dest in data for arg in ("--add-data", f"{src}{os.pathsep}{dest}")],
        *[arg for mod in UNUSED for arg in ("--exclude-module", mod)], ROOT / "packaging" / "blendex.py")
    for cache in (OUT / "dist" / "Blendex").rglob("__pycache__"):
        shutil.rmtree(cache)
    if LINUX:
        print(f"Built {package_linux(OUT / 'dist' / 'Blendex')}")
        return
    run(compiler, "/Q", f"/DAppVersion={VERSION}", ROOT / "packaging" / "blendex.iss")
    print(f"Built {OUT / f'Blendex-{VERSION}-Setup.exe'}")


if __name__ == "__main__":
    main()
