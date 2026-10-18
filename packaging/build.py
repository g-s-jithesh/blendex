import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "release"
sys.path.insert(0, str(ROOT))

from backend.app import VERSION


def run(*cmd: str | Path) -> None:
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)


def main() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found; install Node.js")
    run(npm, "--prefix", "frontend", "ci")
    run(npm, "--prefix", "frontend", "run", "build")
    shutil.rmtree(OUT, ignore_errors=True)
    data = [(ROOT / "frontend" / "dist", "web"), (ROOT / "scripts", "scripts"),
            (ROOT / "backend" / "migrations", "backend/migrations")]
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "Blendex",
        "--distpath", OUT / "dist", "--workpath", OUT / "build", "--specpath", OUT, "--paths", ROOT,
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        *[arg for src, dest in data for arg in ("--add-data", f"{src}{os.pathsep}{dest}")],
        "--exclude-module", "pytest", ROOT / "packaging" / "blendex.py")
    for cache in (OUT / "dist" / "Blendex").rglob("__pycache__"):
        shutil.rmtree(cache)
    archive = shutil.make_archive(str(OUT / f"Blendex-{VERSION}-win64"), "zip", OUT / "dist", "Blendex")
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
