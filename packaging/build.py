import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "release"
ISCC_PATHS = [Path(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
              Path(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe")]
# setuptools only comes in through cffi's compiler shim; clr_loader uses cffi without compiling.
UNUSED = ["pytest", "setuptools", "pkg_resources", "_distutils_hack", "pydoc", "pydoc_data", "_pyrepl",
          "httptools", "watchfiles", "dotenv", "yaml"]
sys.path.insert(0, str(ROOT))

from backend.app import VERSION


def run(*cmd: str | Path) -> None:
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)


def iscc() -> str:
    found = shutil.which("iscc") or next((str(p) for p in ISCC_PATHS if p.is_file()), None)
    if not found:
        raise SystemExit("Inno Setup 6 not found; install it with: winget install JRSoftware.InnoSetup")
    return found


def main() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found; install Node.js")
    compiler = iscc()
    run(npm, "--prefix", "frontend", "ci")
    run(npm, "--prefix", "frontend", "run", "build")
    shutil.rmtree(OUT, ignore_errors=True)
    data = [(ROOT / "frontend" / "dist", "web"), (ROOT / "scripts", "scripts"),
            (ROOT / "backend" / "migrations", "backend/migrations")]
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Blendex",
        "--icon", ROOT / "packaging" / "blendex.ico",
        "--distpath", OUT / "dist", "--workpath", OUT / "build", "--specpath", OUT, "--paths", ROOT,
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        *[arg for src, dest in data for arg in ("--add-data", f"{src}{os.pathsep}{dest}")],
        *[arg for mod in UNUSED for arg in ("--exclude-module", mod)], ROOT / "packaging" / "blendex.py")
    for cache in (OUT / "dist" / "Blendex").rglob("__pycache__"):
        shutil.rmtree(cache)
    run(compiler, "/Q", f"/DAppVersion={VERSION}", ROOT / "packaging" / "blendex.iss")
    print(f"Built {OUT / f'Blendex-{VERSION}-Setup.exe'}")


if __name__ == "__main__":
    main()
