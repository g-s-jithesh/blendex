# 3D DAM — Desktop Digital Asset Manager

Local-first asset manager for 3D project folders. A React UI talks to a local Python daemon over HTTP + WebSocket on `127.0.0.1`. The daemon indexes files, watches for changes, runs headless DCC conversions, and launches DCC apps.

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React 18 + TypeScript (strict) + Vite + Tailwind CSS |
| 3D preview | three.js via `@react-three/fiber` + `@react-three/drei` |
| Backend | Python 3.11+, FastAPI, uvicorn, `watchdog` |
| Database | SQLite via stdlib `sqlite3` (no ORM) |
| Conversion | Blender headless (`blender -b --factory-startup --python-exit-code 1 -P scripts/blender_export_proxy.py -- --input <file> <args>`) builds the thumbnail + GLB proxy for blend/obj/fbx; `.blend` files also show their embedded thumbnail instantly while that job runs. Min Blender 4.2; dev machine has only 5.2.2 LTS. Maya launch-only for now (not installed on dev machine) |
| Tests | `pytest` (backend, parsers, scripts), `vitest` (frontend logic) |
| Desktop shell | Dev: Vite dev server proxies `/api` and `/ws` to the daemon (`start.bat`). Release: PyInstaller `Blendex.exe` (`packaging/blendex.py`) runs the daemon, which serves the built UI, inside its own WebView2 window via `pywebview`; Inno Setup installer (`packaging/blendex.iss`). Build with `packaging/build.py` |

Do not add dependencies outside this table without an entry in `PLAN.md` → Decisions.

## Directory Structure

```
/backend           FastAPI app, watcher, worker queue, db, launcher
  /backend/migrations   Numbered SQL files: 001_init.sql, 002_*.sql
/frontend          React + R3F app
/scripts           Blender scripts: headless proxy export, open-and-import, the `dam_bridge` add-on + its installer
/docs              api_spec.json (OpenAPI 3.1), blend_header_spec.md
/tests             pytest suites + fixture generators
  /tests/fixtures       Generated fixtures (gitignored), never hand-committed binaries
PLAN.md            Single source of truth for phases, tasks, decisions, status
```

## Code Style (strict, all languages)

- **Max 5 comments per file. Zero is preferred.** Names carry intent; comments only explain *why* something non-obvious is required (e.g. a WebGL driver quirk, a Blender API version gate).
- No boilerplate docstrings, no file headers, no commented-out code, no `TODO` without a `PLAN.md` task ID.
- TypeScript: `strict: true`, no `any`. Python: type hints on public functions, `ruff` clean.
- Smallest working diff. No abstractions with a single implementation.

## Runtime Contract

- Daemon binds `127.0.0.1:8765` only. Never `0.0.0.0`.
- Base URL `http://127.0.0.1:8765/api`, WebSocket `ws://127.0.0.1:8765/ws/events`.
- All JSON uses `snake_case`. Timestamps are integer Unix epoch seconds. IDs are integers.
- Errors: HTTP status + `{"error": {"code": "<SNAKE_CODE>", "message": "<human text>"}}`. Codes: `VALIDATION_ERROR` 422 (FastAPI's default 422 body is overridden), `INVALID_PATH` 422, `NOT_FOUND` 404, `ALREADY_EXISTS` 409, `UNSUPPORTED` 409 (ext not convertible / not opened by that app), `LAUNCH_FAILED` 500, `BRIDGE_FAILED` 502 (a Blender session or bridge install failed), `INTERNAL` 500.
- `ext` is lowercase without the dot (`blend`). `GET /assets` orders by `name, id`; `q` is a case-insensitive substring match on `name`; `favorite=true|false` and `tag=<tag>` (exact, case-insensitive) filter.
- Tags are trimmed, lowercased and de-duplicated; each is 1–40 printable characters without commas, at most 20 per asset; violations are `VALIDATION_ERROR`.

### REST

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","version":str}` |
| GET | `/libraries` | — | `Library[]` |
| POST | `/libraries` | `{"path": str}` | `201 Library` (starts initial scan) |
| DELETE | `/libraries/{id}` | — | `204` (removes index rows, never user files) |
| GET | `/assets` | `library_id?, q?, ext?, favorite?, tag?, limit=100, offset=0` | `{"items": Asset[], "total": int}` |
| GET | `/assets/{id}` | — | `Asset` |
| GET | `/assets/{id}/thumbnail` | `v?` (ignored) | `image/png` or `404` |
| GET | `/assets/{id}/proxy` | `v?` (ignored) | `model/gltf-binary` or `404` |
| POST | `/assets/{id}/convert` | — | `202` (re-queue conversion) |
| PUT | `/assets/{id}/favorite` | `{"favorite": bool}` | `Asset` (broadcasts `asset.updated`) |
| PUT | `/assets/{id}/tags` | `{"tags": str[]}` (replaces the set) | `Asset` (broadcasts `asset.updated`) |
| GET | `/tags` | — | `{"tag": str, "count": int}[]` sorted by tag |
| GET | `/queue` | — | `QueueState` |
| POST | `/queue/pause` | — | `QueueState` (no new Blender jobs start; running ones finish) |
| POST | `/queue/resume` | — | `QueueState` |
| GET | `/apps` | — | `App[]` (detected DCC executables) |
| POST | `/launch` | `{"asset_id": int, "app_id": str}` | `202` |
| GET | `/sessions` | — | `Session[]` (running Blenders with the bridge; dead session files are deleted) |
| POST | `/sessions/{id}/import` | `{"asset_id": int}` | `{"imported": str[]}` |
| GET | `/bridges` | — | `Bridge[]` (one per detected Blender) |
| POST | `/bridges/{app_id}` | — | `204` (installs + enables the add-on in that Blender) |

`docs/api_spec.json` is the authoritative OpenAPI version of this table; keep both in sync.

### Proxy script CLI (`scripts/blender_export_proxy.py`)

```
blender -b --factory-startup --python-exit-code 1 -P scripts/blender_export_proxy.py -- \
  --input <file.blend|.obj|.fbx> --out-png <path> --out-meta <path> [--out-glb <path>] [--decimate 0.5] [--thumb-size 512]
```

No `--out-glb` = thumbnail-only mode. `.blend` is opened with `open_mainfile(load_ui=False)`; obj/fbx are imported into the empty factory scene. Any edit/sculpt/pose mode is switched to object mode after loading. The thumbnail always uses a temporary 3/4-view camera framed on all visible meshes; the scene camera is ignored (workfile cameras often point away from the mesh).
Exit code 0 = success, non-zero = failure. Always writes `--out-meta` (also on failure, when possible):
`{"ok": bool, "blender_version": "4.2.0", "poly_count": int|null, "proxy_poly_count": int|null, "bounds": {"min": [x,y,z], "max": [x,y,z]}|null, "thumb_size": int, "error": str|null}`.
`proxy_poly_count` is `null` in thumbnail-only mode. The backend reads the meta file, never Blender's stdout.

### Preview rules

| ext | Preview | Result |
|---|---|---|
| `blend` | Embedded thumbnail (`TEST` block, byte rules in `docs/blend_header_spec.md`) extracted inline as `.cache/<id>.png` (temp + `os.replace`), then a full Blender job replaces it | instantly `pending` + `has_thumbnail` true; after the job `ready`, `has_thumbnail` + `has_proxy` true, `poly_count` from meta |
| `blend`, no/unparseable preview | Full Blender job only | `ready`, `has_thumbnail` + `has_proxy` true, `poly_count` from meta |
| `obj`, `fbx` | Full Blender job: thumbnail + decimated Draco GLB | `ready`, `has_thumbnail` + `has_proxy` true, `poly_count` from meta |
| other indexed exts | none | `unsupported` |

- The hover 3D viewport opens only when `has_proxy` is true; until the job finishes a `.blend` card shows its embedded thumbnail.
- Inline extraction emits `asset.added` / `asset.updated` (still `pending`), then the job emits `conversion.*`. `POST /convert` re-runs the same routing. On startup, `.blend` assets that are `ready` without a proxy (indexed under the earlier image-only rule) are re-queued once.
- Clients request `/thumbnail?v=<mtime>` and `/proxy?v=<mtime>` to bust browser caches on save; the backend ignores `v`.

### Shapes

```ts
type Library = { id: number; path: string; created_at: number };
type AssetStatus = "pending" | "converting" | "ready" | "failed" | "unsupported";
type Asset = {
  id: number; library_id: number; path: string; name: string; ext: string;
  size: number; mtime: number; dcc_version: string | null;
  status: AssetStatus; poly_count: number | null; error: string | null;
  has_thumbnail: boolean; has_proxy: boolean; favorite: boolean; tags: string[];
};
type TagCount = { tag: string; count: number };
type App = { id: string; name: string; version: string | null; executable: string; exts: string[] };
type Session = { id: string; app: string; version: string | null; pid: number; file: string | null; exts: string[] };
type Bridge = { app_id: string; name: string; installed: boolean };
type QueueState = { queued: number; running: number; paused: boolean };
```

### Open with / import

- `/apps` lists detected Blender (`blend` opens directly; obj/fbx/glb/gltf/stl/ply/usd*/abc start a new Blender that runs `scripts/blender_open_import.py`), Maya (`ma`/`mb`, untested: not installed on the dev machine), and on Windows `explorer` (show in folder) and `open-with` (Windows app picker) for every indexed ext.
- Importing into an already-open Blender needs the `scripts/dam_bridge.py` add-on, installed via `POST /bridges/{app_id}` (runs `scripts/install_dam_bridge.py` in that Blender, saves its user prefs). Each GUI session listens on `127.0.0.1:<random port>` and writes `<APPDATA>/dam/sessions/blender-<pid>.json` (`DAM_SESSIONS` overrides) with the port and a random token.
- Bridge protocol: one JSON line per connection `{"token", "cmd": "info"|"import", "path"?}`, one JSON line back. Commands run on Blender's main thread via a timer; there is no command that executes code. `.blend` imports append its objects; every import is one undo step.
- Tests that run a real Blender installer must set `BLENDER_USER_RESOURCES` to a temp dir; Blender 5.2 ignores `BLENDER_USER_CONFIG` and would rewrite the user's real preferences.

### WebSocket events (server → client only)

`{"type": <event>, "asset_id": int, "data": {...}}` where `<event>` is one of
`asset.added`, `asset.updated`, `asset.removed`, `conversion.started`, `conversion.done`, `conversion.failed`.
`data` is always the full `Asset` (last known state for `asset.removed`).
The one non-asset event is `{"type": "queue.updated", "data": QueueState}` (no `asset_id`), sent when queue counts or `paused` change, coalesced to at most one per 100 ms. Pause is in memory only; a daemon restart resumes the queue.
Clients refetch on reconnect; events are not replayed.

Contract changes go through the architect: update this section and `PLAN.md` → Decisions in the same change.

## SQLite Rules

- One DB file: `%APPDATA%/dam/dam.sqlite` (Windows), `~/.local/share/dam/dam.sqlite` elsewhere. Never inside a user library.
- On connect: `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`
- Tables are `STRICT`. `INTEGER PRIMARY KEY` ids. Foreign keys with `ON DELETE CASCADE`.
- Paths stored absolute, resolved, forward-slash normalized; `UNIQUE` on `assets.path`.
- Change detection by `(size, mtime)`; no content hashing on the hot path.
- Schema (`001_init.sql`): `libraries`, `assets` (identity + `current_version_id`), `versions` (one row per seen `(size, mtime)`: header `dcc_version`, poly counts, conversion `status`/`error`, `has_thumbnail`/`has_proxy`), `dcc_apps` (`exts` is JSON text). `002_favorites_tags.sql` adds `assets.favorite` (0/1) and `asset_tags(asset_id, tag)` (cascade on asset delete, index on `tag`); both belong to the asset identity, so they survive file edits. The API `Asset` is `assets JOIN versions ON current_version_id` plus its tags; revision history is not exposed.
- Schema changes only via a new numbered file in `backend/migrations/`; applied in order, tracked with `PRAGMA user_version`. Never edit a shipped migration.
- Parameterized queries only. No string-built SQL.

## Cache Rules

- Per library: `<library_root>/.cache/<asset_id>.glb` and `<asset_id>.png`. Set the hidden attribute on Windows.
- The watcher ignores `.cache/`. Deleting `.cache/` must be safe; the daemon regenerates on demand.
- Conversion writes to a temp file then `os.replace` to the final name, so the UI never reads a partial `.glb`.

## Security Boundaries

- Subprocesses use argv lists, never `shell=True`. The only exceptions are `explorer`/`open-with`, which parse their own raw command line, so they get a string (still no shell; Windows paths cannot contain `"`). `/launch` only accepts an `asset_id` that exists in the DB and an `app_id` from `/apps`.
- Bridge sessions bind `127.0.0.1` only, require the per-session token, accept only `info`/`import` of known 3D exts, and session ids must match `blender-<digits>` before any file lookup.
- Header parsers read a bounded byte count and never trust lengths from the file.
- File-serving endpoints only serve files resolved inside a registered library's `.cache/`.
- The worker refuses to write previews when a library's `.cache` resolves outside the library (a symlink or junction): the asset fails with a clear error instead.

## Agents & Ownership

| Agent | Owns |
|---|---|
| `architect` | `PLAN.md`, `CLAUDE.md` (contracts/schema sections), `backend/migrations/`, `docs/api_spec.json` |
| `dcc-researcher` | `scripts/`, `docs/blend_header_spec.md` |
| `backend-coder` | `backend/` (except `migrations/`) |
| `frontend-coder` | `frontend/` |
| `qa-tester` | `tests/` |

Agents edit only files they own. Need a change elsewhere → add a task to `PLAN.md` → Backlog addressed to the owner. Every agent reports status in `PLAN.md` per the protocol defined there.
