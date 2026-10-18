import asyncio
import os
import sqlite3
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from .blend_header import parse_blend_header

EXTS = {"blend", "ma", "mb", "fbx", "obj", "glb", "gltf", "usd", "usda", "usdc", "usdz", "abc", "stl", "ply"}
PROXY_EXTS = {"blend", "obj", "fbx"}
CONVERTIBLE = PROXY_EXTS
DEBOUNCE = 0.5


def ignored(rel: Path) -> bool:
    return any(part.startswith((".", "~")) for part in rel.parts)


def _ext(path: str) -> str:
    return Path(path).suffix[1:].lower()


def _header_version(path: str) -> str | None:
    try:
        info = parse_blend_header(path)
    except OSError:
        return None
    return info["version"] if info else None


def walk(root: str, start: str, known: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int, str | None]]:
    paths = [start] if os.path.isfile(start) else []
    for d, dirs, files in os.walk(start):
        dirs[:] = [x for x in dirs if not x.startswith((".", "~"))]
        paths += [os.path.join(d, f) for f in files]
    out = {}
    for p in paths:
        if _ext(p) not in EXTS or ignored(Path(p).relative_to(root)):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        key, sm = Path(p).as_posix(), (st.st_size, int(st.st_mtime))
        ver = _header_version(p) if _ext(p) == "blend" and known.get(key) != sm else None
        out[key] = (*sm, ver)
    return out


class _Handler(FileSystemEventHandler):
    def __init__(self, indexer: "Indexer", lib: dict, loop: asyncio.AbstractEventLoop):
        self.indexer, self.lib, self.loop = indexer, lib, loop

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed_no_write") or (event.is_directory and event.event_type == "modified"):
            return
        for p in (event.src_path, getattr(event, "dest_path", "")):
            p = os.fsdecode(p)
            if p and (event.is_directory or event.event_type == "deleted" or _ext(p) in EXTS):
                self.loop.call_soon_threadsafe(self.indexer.schedule, self.lib, p)


class Indexer:
    def __init__(self, conn: sqlite3.Connection, publish: Callable[[str, dict | None], None],
                 route: Callable[[int, str | None], Awaitable[None]]):
        self.conn, self.publish, self.route = conn, publish, route
        self.observers: dict[int, Observer] = {}
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.tasks: set[asyncio.Task] = set()

    def busy(self) -> bool:
        return bool(self.timers or self.tasks)

    def spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def start(self, lib: dict) -> None:
        obs = Observer()
        obs.schedule(_Handler(self, lib, asyncio.get_running_loop()), lib["path"], recursive=True)
        obs.start()
        self.observers[lib["id"]] = obs
        self.spawn(self._initial(lib))

    async def _initial(self, lib: dict) -> None:
        routed = await self.sync(lib, lib["path"])
        for asset_id in db.reroute_ids(self.conn, lib["id"]):
            if asset_id not in routed:
                await self.route(asset_id, "asset.updated")

    def unwatch(self, library_id: int) -> None:
        if obs := self.observers.pop(library_id, None):
            obs.stop()
            obs.join()

    def stop(self) -> None:
        for library_id in list(self.observers):
            self.unwatch(library_id)
        for t in [*self.timers.values(), *self.tasks]:
            t.cancel()
        self.timers.clear()

    def schedule(self, lib: dict, path: str) -> None:
        if lib["id"] not in self.observers or ignored(Path(path).relative_to(lib["path"])):
            return
        if old := self.timers.pop(path, None):
            old.cancel()
        self.timers[path] = asyncio.get_running_loop().call_later(DEBOUNCE, self._fire, lib, path)

    def _fire(self, lib: dict, path: str) -> None:
        self.timers.pop(path, None)
        self.spawn(self.sync(lib, path))

    async def sync(self, lib: dict, start: str) -> set[int]:
        start = Path(start).as_posix()
        known = db.snapshot(self.conn, lib["id"], start)
        found = await asyncio.to_thread(walk, lib["path"], start, known)
        if lib["id"] not in self.observers:
            return set()
        changed = []
        with self.conn:
            for path, (size, mtime, ver) in found.items():
                if known.get(path) == (size, mtime):
                    continue
                status = "pending" if _ext(path) in CONVERTIBLE else "unsupported"
                if r := db.upsert_asset(self.conn, lib["id"], path, size, mtime, ver, status):
                    changed.append((*r, path))
            removed = [a for p in known.keys() - found.keys() for a in db.remove_assets(self.conn, p)]
        for asset in removed:
            self.publish("asset.removed", asset)
        for asset_id, created, path in changed:
            event = "asset.added" if created else "asset.updated"
            if _ext(path) in CONVERTIBLE:
                await self.route(asset_id, event)
            else:
                self.publish(event, db.get_asset(self.conn, asset_id))
        return {asset_id for asset_id, _, _ in changed}
