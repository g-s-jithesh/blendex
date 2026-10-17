import asyncio
import contextlib
import json
import logging
import os
import secrets
import signal
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import db, dcc
from .blend_preview import read_embedded_preview, rgba_to_png
from .indexer import CONVERTIBLE, PROXY_EXTS

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blender_export_proxy.py"
log = logging.getLogger(__name__)


def hide(path: Path) -> None:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        attrs = k32.GetFileAttributesW(str(path))
        if attrs != -1 and attrs != 0xFFFFFFFF:
            k32.SetFileAttributesW(str(path), attrs | 0x2)


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        # Windows venv python.exe and some launchers re-spawn the real process; kill the whole tree.
        k = await asyncio.create_subprocess_exec("taskkill", "/F", "/T", "/PID", str(proc.pid),
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await k.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()


def cache_dir(library_path: str) -> Path:
    cache = Path(library_path) / ".cache"
    cache.mkdir(exist_ok=True)
    hide(cache)
    return cache


def write_embedded_preview(src: str, library_path: str, asset_id: int) -> bool:
    tmp = None
    try:
        cache = cache_dir(library_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(cache / f"{asset_id}.glb")
        if not (preview := read_embedded_preview(src)):
            return False
        tmp = cache / f"{asset_id}.{secrets.token_hex(4)}.tmp.png"
        tmp.write_bytes(rgba_to_png(*preview))
        os.replace(tmp, cache / f"{asset_id}.png")
        return True
    except OSError:
        log.exception("writing embedded preview of asset %s failed", asset_id)
        return False
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def _read_meta(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


class Worker:
    def __init__(self, conn: sqlite3.Connection, publish: Callable[[str, dict | None], None]):
        self.conn, self.publish = conn, publish
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.queued: set[int] = set()
        self.running = 0
        self.procs: set[asyncio.subprocess.Process] = set()
        self.concurrency = int(os.environ.get("DAM_WORKERS") or max(1, (os.cpu_count() or 1) // 4))
        self.timeout = float(os.environ.get("DAM_JOB_TIMEOUT") or 600)
        self.tasks: list[asyncio.Task] = []

    def busy(self) -> bool:
        return bool(self.queued or self.running)

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(self.concurrency)]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def enqueue(self, asset_id: int) -> None:
        if asset_id not in self.queued:
            self.queued.add(asset_id)
            self.queue.put_nowait(asset_id)

    async def route(self, asset_id: int, event: str | None = None) -> None:
        asset = db.get_asset(self.conn, asset_id)
        if not asset or asset["ext"] not in CONVERTIBLE:
            return
        inline = False
        if asset["ext"] == "blend":
            vid = db.current_version_id(self.conn, asset_id)
            lib_path = db.get_library(self.conn, asset["library_id"])["path"]
            inline = await asyncio.to_thread(write_embedded_preview, asset["path"], lib_path, asset_id)
            if inline and db.current_version_id(self.conn, asset_id) == vid:
                db.set_result(self.conn, vid, None, None, False)
                self.conn.commit()
        if event:
            self.publish(event, db.get_asset(self.conn, asset_id))
        if not inline:
            self.enqueue(asset_id)

    async def _loop(self) -> None:
        while True:
            asset_id = await self.queue.get()
            self.queued.discard(asset_id)
            self.running += 1
            try:
                await self.convert(asset_id)
            except Exception as e:
                log.exception("conversion of asset %s crashed", asset_id)
                self.conn.rollback()
                if vid := db.current_version_id(self.conn, asset_id):
                    db.set_status(self.conn, vid, "failed", f"{type(e).__name__}: {e}")
                    self._emit("conversion.failed", asset_id)
            finally:
                self.running -= 1

    def _emit(self, event: str, asset_id: int) -> None:
        self.conn.commit()
        self.publish(event, db.get_asset(self.conn, asset_id))

    async def convert(self, asset_id: int) -> None:
        asset = db.get_asset(self.conn, asset_id)
        if not asset or asset["ext"] not in CONVERTIBLE:
            return
        vid = db.current_version_id(self.conn, asset_id)
        cmd = dcc.blender_command(self.conn)
        if not cmd:
            db.set_status(self.conn, vid, "failed", "Blender not found; install Blender or set DAM_BLENDER")
            return self._emit("conversion.failed", asset_id)
        db.set_status(self.conn, vid, "converting")
        self._emit("conversion.started", asset_id)
        full = asset["ext"] in PROXY_EXTS
        cache = cache_dir(db.get_library(self.conn, asset["library_id"])["path"])
        tmp = cache / f"{asset_id}.{secrets.token_hex(4)}.tmp"
        glb, png, meta_path = f"{tmp}.glb", f"{tmp}.png", f"{tmp}.json"
        try:
            rc = await self._run([*cmd, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(SCRIPT),
                                  "--", "--input", asset["path"], "--out-png", png, "--out-meta", meta_path,
                                  *(["--out-glb", glb] if full else [])])
            meta = _read_meta(meta_path)
            if db.current_version_id(self.conn, asset_id) != vid:
                return
            if rc == 0 and meta.get("ok") and os.path.isfile(png) and (not full or os.path.isfile(glb)):
                os.replace(png, cache / f"{asset_id}.png")
                if full:
                    os.replace(glb, cache / f"{asset_id}.glb")
                db.set_result(self.conn, vid, meta.get("poly_count"), meta.get("proxy_poly_count") if full else None,
                              full)
                return self._emit("conversion.done", asset_id)
            error = meta.get("error") or (f"timed out after {self.timeout:g}s" if rc is None
                                          else f"exit code {rc}" if rc else "proxy script wrote no output")
            db.set_status(self.conn, vid, "failed", str(error))
            self._emit("conversion.failed", asset_id)
        finally:
            for f in (glb, png, meta_path):
                with contextlib.suppress(OSError):
                    os.remove(f)

    async def _run(self, argv: list[str]) -> int | None:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        proc = await asyncio.create_subprocess_exec(*argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL, **kw)
        self.procs.add(proc)
        try:
            return await asyncio.wait_for(proc.wait(), self.timeout)
        except TimeoutError:
            return None
        finally:
            if proc.returncode is None:
                await kill_tree(proc)
            self.procs.discard(proc)
