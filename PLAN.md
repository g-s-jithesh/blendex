# PLAN

Owner: `architect`. Other agents edit only the **Status** column of their own tasks and append to **Log**.

## Status Protocol

1. Pick a task assigned to you with status `todo`. Set it to `doing`.
2. When finished, set `done` (or `blocked`) and append one Log line:
   `- YYYY-MM-DD [agent] T<id> <done|blocked>: <one-line result>. Files: <paths>. Verify: <command>`
3. Blocked on another agent? Set `blocked`, add a Backlog line `- [for:<agent>] <need> (blocks T<id>)`.
4. Never start work that has no task ID. Out-of-scope ideas go to Backlog for the architect to triage.

## Current Phase

**Phases 1–5 complete.** All suites green; pushed to GitHub.

## Phases

### Phase 1 — Research & Architecture
- [x] `PLAN.md` phases, tasks, decisions (architect)
- [x] SQLite schema `backend/migrations/001_init.sql` (architect)
- [x] `docs/api_spec.json` OpenAPI 3.1 (architect)
- [x] `scripts/blender_export_proxy.py` (dcc-researcher) — written; real-Blender verification moved to T18 (Blender 5.2)
- [x] `docs/blend_header_spec.md` (dcc-researcher) — legacy 12-byte + 5.0 v1 17-byte headers, gzip/zstd; reference parser tested on 7 real headers

### Phase 2 — Core Backend & 3D Frontend (parallel)
- [x] backend-coder: DB models/CRUD + migration runner (T1)
- [x] backend-coder: `.blend` header parser (T2)
- [x] backend-coder: FastAPI app, REST, error envelope (T3)
- [x] backend-coder: watchdog monitor, scanner, `/ws/events` (T4)
- [x] backend-coder: async worker queue running the proxy script into `.cache/` (T5)
- [x] backend-coder: DCC app detection, `/apps`, `/launch` via `subprocess.Popen` (T6)
- [x] frontend-coder: Vite/React/Tailwind shell, API client, dev proxy (T7)
- [x] frontend-coder: asset grid, filter/search, "Open in Blender (vX.X)" dropdown (T8)
- [x] frontend-coder: `AssetCard` strict 2000 ms hover timer (T9)
- [x] frontend-coder: shared `HoverViewport3D` with OrbitControls, Wireframe/Clay, explicit disposal (T10)

### Phase 3 — Testing & Stress
- [x] qa-tester: pytest header parsing (T11) — rolled into T23
- [x] qa-tester: pytest SQLite indexing (T12)
- [x] qa-tester: pytest API endpoints (T13)
- [x] qa-tester: hover-timer tests (T14)
- [x] qa-tester: 20-card rapid hover leaks no WebGL contexts (T15)
- [x] qa-tester: no zombie Blender processes (T16)

### Phase 4 — Integration & Self-Healing
- [x] architect: route each failure to its owner as a fix task; re-run QA until green (T17)

### Phase 5 — Preview redesign
- [x] dcc-researcher: proxy script `--input`, obj/fbx import, thumbnail-only mode on Blender 5.2; spec "Embedded preview" section (T18)
- [x] backend-coder: embedded-preview extraction + stdlib PNG writer (T19)
- [x] backend-coder: job routing by ext, new argv, `/convert` routing, ignore `v` (T20)
- [x] backend-coder: header parser corrupt-stream `None` fix (T21)
- [x] frontend-coder: `?v=<mtime>` on thumbnail/proxy URLs; THREE.Clock warning (T22)
- [x] qa-tester: fake Blender + unit tests for argv/routing/parser (T23)
- [x] qa-tester: real Blender 5.2 fixtures, embedded-preview tests, `@pytest.mark.blender` run (T24)
- [x] qa-tester: full re-run incl. vitest + Playwright (T25)

## Tasks

| ID | Phase | Owner | Task | Verify | Status |
|---|---|---|---|---|---|
| T1 | 2 | backend-coder | `backend/db.py`: connect with the CLAUDE.md pragmas, apply `migrations/*.sql` in order while `user_version` < file number, CRUD for libraries/assets/versions/dcc_apps; `Asset` row = `assets JOIN versions ON current_version_id` | `python -c "from backend.db import connect;c=connect(':memory:');print(c.execute('pragma user_version').fetchone())"` → `(1,)` | done |
| T2 | 2 | backend-coder | `backend/blend_header.py`: `parse_blend_header(path) -> dict \| None` exactly per `docs/blend_header_spec.md` (its `version` feeds `dcc_version`); bounded read, handles gzip/zstd-compressed headers as the spec says | `python -m backend.blend_header <any.blend>` prints e.g. `4.2` | done |
| T3 | 2 | backend-coder | `backend/app.py`: FastAPI on `127.0.0.1:8765`, `/api/health`, `/libraries` (GET/POST 201/DELETE), `/assets` (filters, `name, id` order, total), `/assets/{id}`; every error (incl. 422) uses the envelope | `uvicorn backend.app:app --host 127.0.0.1 --port 8765` then `curl -s 127.0.0.1:8765/api/health` | done |
| T4 | 2 | backend-coder | Initial scan + `watchdog` monitor per library (ignores `.cache/`); new `(size, mtime)` → new `versions` row + `current_version_id`, header `dcc_version`; `/ws/events` broadcasts `asset.*` with full Asset | Add a library, touch a `.blend`, observe `asset.updated` on `/ws/events` | done |
| T5 | 2 | backend-coder | Async worker queue: runs proxy script (argv list, timeout, kill on timeout/shutdown) to temp files then `os.replace` into `.cache/<id>.glb/.png` (hidden on Windows); reads `--out-meta`; updates version status; `/convert`, `/thumbnail`, `/proxy` (cache-only serving); `conversion.*` events. Blender path from `dcc_apps`, overridable by env `DAM_BLENDER` | Add a library with a `.blend`; `GET /api/assets/{id}/proxy` returns `model/gltf-binary` | done |
| T6 | 2 | backend-coder | Detect Blender installs (Windows Program Files + PATH) into `dcc_apps` (`blender-X.Y`, exts `["blend"]`); `GET /apps`; `POST /launch` validates asset exists, app exists, ext in app exts, then `Popen([exe, path])` | `curl -s 127.0.0.1:8765/api/apps` lists Blender | done |
| T7 | 2 | frontend-coder | Vite + React 18 + TS strict + Tailwind in `frontend/`; dev server proxies `/api` and `/ws` to `127.0.0.1:8765`; typed client + `useEvents` WS hook from CLAUDE.md Shapes; vitest config also includes `../tests/frontend/**/*.test.ts?(x)` with jsdom | `npm --prefix frontend run build` | done |
| T8 | 2 | frontend-coder | Asset grid with library/ext filter and debounced search over `GET /assets`; live updates from WS; per-card "Open in Blender (vX.X)" dropdown from `/apps` filtered by `ext` → `POST /launch` | `npm --prefix frontend run build` + manual check against running daemon | done |
| T9 | 2 | frontend-coder | `AssetCard` hover: preview starts exactly 2000 ms after enter; leave calls `clearTimeout` and aborts the proxy fetch via `AbortController`; at most one active preview | `npm --prefix frontend run build` | done |
| T10 | 2 | frontend-coder | Single shared `HoverViewport3D` R3F `Canvas` (mounted once, repositioned over the hovered card) with `OrbitControls`, Wireframe/Clay toggle; on model swap/unmount dispose every geometry, material and texture; on app unmount `renderer.dispose()` + `forceContextLoss()` | `npm --prefix frontend run build` | done |
| T11 | 3 | qa-tester | Fixture generator for synthetic `.blend` headers (plain, gzip, zstd, truncated, garbage) + `tests/test_blend_header.py` | `pytest tests/test_blend_header.py` | done (rolled into T23) |
| T12 | 3 | qa-tester | `tests/test_indexing.py`: migration applies (`user_version` 1, STRICT), scan inserts, `(size, mtime)` change adds a version, library delete cascades, `.cache/` ignored | `pytest tests/test_indexing.py` | done |
| T13 | 3 | qa-tester | `tests/test_api.py` via FastAPI TestClient: every endpoint in `docs/api_spec.json`, error envelope codes, `/launch` rejects unknown asset/app/ext, Popen called with argv list (monkeypatched) | `pytest tests/test_api.py` | done |
| T14 | 3 | qa-tester | `tests/frontend/hover.test.tsx` (vitest fake timers): no preview at 1999 ms, preview at 2000 ms, leave before 2000 ms clears the timer, leave after start aborts the fetch | `npm --prefix frontend run test -- hover` | done |
| T15 | 3 | qa-tester | Playwright (Chromium, real WebGL) against the Vite build with a stubbed `/api` serving 20 assets + a small `.glb`: rapid enter/leave across 20 cards, then assert live WebGL contexts ≤ 1 (0 after leave) and `renderer.info.memory` geometries/textures back to baseline, no console errors. Plus vitest unit test that every created geometry/material/texture is disposed | `npx --prefix tests/e2e playwright test -c tests/e2e` | done |
| T16 | 3 | qa-tester | `tests/test_worker.py`: with `DAM_BLENDER` pointed at a sleeping fake script, queue 20 conversions then shut down / time out; assert every spawned process has exited; hovering never spawns a process | `pytest tests/test_worker.py` | done |
| T17 | 4 | architect | Triage Phase 3 + Phase 5 failures into fix tasks for owners; re-run full suite until green | `pytest && npm --prefix frontend run test` | done |
| T18 | 5 | dcc-researcher | `scripts/blender_export_proxy.py` per CLAUDE.md → Proxy script CLI: `--input` (blend via `open_mainfile(load_ui=False)`, obj/fbx imported into the factory scene), optional `--out-glb` (absent = thumbnail-only, `proxy_poly_count` null), scene camera else framed 3/4 view. `docs/blend_header_spec.md` → "Embedded preview": `TEST` block location, layout, preview type None, bounded read + length checks, compressed files. Verified on Blender 5.2 | `"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" -b --factory-startup --python-exit-code 1 -P scripts/blender_export_proxy.py -- --input <cube.obj> --out-png t.png --out-meta m.json --out-glb t.glb` exits 0 with meta `ok:true`; same with a `.blend` and no `--out-glb` | done |
| T19 | 5 | backend-coder | `backend/blend_preview.py`: extract the embedded preview per spec "Embedded preview" (bounded read, never trust lengths, gzip/zstd via the header parser's decoders) → `None` when absent/unparseable/decoder unavailable; write RGBA PNG with stdlib `zlib` + `struct` (no Pillow) to temp + `os.replace` into `.cache/<id>.png`. Starts after T18's spec section lands | `python -m backend.blend_preview <any.blend> out.png` writes a 128x128 PNG; exits 1 for a no-preview file | done |
| T20 | 5 | backend-coder | Routing per CLAUDE.md → Preview rules: blend → T19 inline (`ready`, `has_thumbnail`, emits only `asset.*`) else thumbnail-only job; obj/fbx → full job; others `unsupported`. New argv (`--input`; no `--out-glb` for thumbnail-only). `/convert` re-runs routing (409 for others). `/thumbnail` + `/proxy` accept and ignore `v` | `ruff check backend` + add a library holding blend/obj/fbx/stl; `curl -s "127.0.0.1:8765/api/assets?limit=10"` shows ready/ready/ready/unsupported, blend `has_proxy:false` | done |
| T21 | 5 | backend-coder | `backend/blend_header.py`: corrupt/undecodable compressed stream → `None`; partial `{"compressed": "zstd", ...None}` only when the zstd decoder is unavailable | `pytest tests/test_blend_header.py` | done |
| T22 | 5 | frontend-coder | `thumbnailUrl`/`proxyUrl` send `?v=<mtime>`; resolve the THREE.Clock deprecation warning (align R3F/three, or accept via a Backlog request) | `npm --prefix frontend run build && npm --prefix frontend run test` | done |
| T23 | 5 | qa-tester | Update `tests/fake_blender.py`, `test_worker.py`, `test_api.py` for new argv + routing (blend inline/fallback, obj/fbx full job, others unsupported/409, `v` ignored, blend never `has_proxy`); finish T11 header tests incl. corrupt zstd → `None` | `.venv/Scripts/python -m pytest -q -m "not blender"` | done |
| T24 | 5 | qa-tester | `tests/fixtures/make_fixtures.py` generates real fixtures with Blender 5.2 (blend with preview plain + compressed, blend without preview, obj, fbx); embedded-preview extraction tests (128x128, PNG decodes, no-preview → `None` → fallback job); run `@pytest.mark.blender` tests for real | `.venv/Scripts/python -m pytest -q -rs -m blender` with 0 skipped | done |
| T25 | 5 | qa-tester | Full re-run after T18–T24: pytest, vitest, Playwright (blend card shows thumbnail and opens no 3D viewport; obj/fbx open the viewport) | `.venv/Scripts/python -m pytest -q && npm --prefix frontend run test && npx --prefix tests/e2e playwright test -c tests/e2e` | done |

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-10-17 | SQLite via stdlib `sqlite3`, no ORM | Small schema, zero dependency, explicit SQL |
| 2026-10-17 | No desktop shell in Phases 1–4: browser-served dev build, Vite dev server proxies `/api` + `/ws` to the daemon. Tauri vs Electron deferred to a later packaging phase | User's phases exclude packaging; the daemon/UI contract is identical either way |
| 2026-10-17 | Schema: `assets` holds identity + `current_version_id`; `versions` holds per-`(size, mtime)` revision state (dcc_version, poly counts, status, error, cache flags). History is internal; `Asset` shape unchanged | Keeps API stable; revision count not needed by any core feature |
| 2026-10-17 | Error codes fixed: `VALIDATION_ERROR`, `INVALID_PATH`, `NOT_FOUND`, `ALREADY_EXISTS`, `UNSUPPORTED`, `LAUNCH_FAILED`, `INTERNAL`; `POST /libraries` returns 201; `ext` lowercase without dot; `/assets` ordered by `name, id` | Frontend and tests need deterministic contract values |
| 2026-10-17 | WS event `data` is always the full `Asset` (last known state for `asset.removed`) | One event shape for the client |
| 2026-10-17 | Vitest tests live in `tests/frontend/` (owned by qa-tester); frontend vitest config includes them. Dev deps allowed: `jsdom`, `@testing-library/react` (frontend), `httpx` (FastAPI TestClient) | Keeps test ownership with qa-tester; minimum needed to run the stack's test tools |
| 2026-10-17 | T15 uses Playwright (dev-only, `tests/e2e/`) for real-browser WebGL leak checks | User's Phase 3 requires verifying real WebGL contexts don't leak; mocks can't prove that |
| 2026-10-17 | Minimum supported Blender 4.2; Blender argv adds `--python-exit-code 1` before `-P` | 4.2 LTS is oldest observed; uncaught script exceptions otherwise exit 0 |
| 2026-10-17 | Hover never triggers conversion; conversions are queued by the indexer and `/convert` only. `DAM_BLENDER` env overrides the Blender executable | Rapid hover cannot spawn Blender processes; lets tests use a fake executable |
| 2026-10-17 | Indexed exts: blend ma mb fbx obj glb gltf usd usda usdc usdz abc stl ply; only `blend` converts, rest `unsupported`. `Asset.name` = file stem | backend-coder gap decision |
| 2026-10-17 | Overlapping/relative library paths → 422 `INVALID_PATH`; other 4xx → `VALIDATION_ERROR`, 5xx → `INTERNAL`; `/convert` + `/launch` return empty 202; ignored paths = any component starting with `.` or `~` | backend-coder gap decision |
| 2026-10-17 | Missing cache file on `/proxy`/`/thumbnail` → reset to `pending`, re-queue, 404. Converter = `DAM_BLENDER` else highest detected Blender. Never run uvicorn with `--reload` on Windows (Selector loop breaks subprocesses) | backend-coder gap decision |
| 2026-10-18 | `.blend` = image-only preview, never a 3D proxy: indexer extracts the embedded `TEST`-block thumbnail (Blender 5.2 saves 128x128 RGBA camera view) to `.cache/<id>.png` (temp + `os.replace`) → `ready`, `has_thumbnail` true, `has_proxy` false, `poly_count` null. No Blender process | User-approved; every saved `.blend` already carries a preview, so indexing needs no Blender |
| 2026-10-18 | `.blend` with no embedded preview (type None or unparseable, incl. zstd decoder unavailable) → thumbnail-only Blender job → `ready`, `has_thumbnail` true, `has_proxy` false, `poly_count` from meta | User-approved fallback |
| 2026-10-18 | 3D proxies only for `obj` + `fbx` (import → thumbnail + decimated Draco GLB); other indexed exts stay `unsupported`. Supersedes "only `blend` converts" (2026-10-17) | User-approved |
| 2026-10-18 | Proxy script CLI: `--input <file>` after `--` (no positional file); no `--out-glb` = thumbnail-only (`proxy_poly_count` null); blend via `open_mainfile(load_ui=False)`, obj/fbx imported into factory scene; scene camera else framed 3/4 view. Meta keys unchanged | User-approved; one argv shape for blend/obj/fbx |
| 2026-10-18 | Header parser: corrupt/undecodable compressed stream → `None`; partial `{"compressed": "zstd", ...None}` only when the zstd decoder is unavailable (Python < 3.14). Resolves the T11 contradiction | User-approved |
| 2026-10-18 | `/thumbnail` and `/proxy` accept optional `v` query param (client sends `mtime`); backend ignores it | Thumbnails change on every save; stale `<img>` cache would be a visible bug |
| 2026-10-18 | WebGL debug baseline after leave is `{geometries:0, textures:<=1}`; the 1 is three r186's internal 16x16 DFG LUT (GL-verified, lost context). No code change | User-approved; not a leak |
| 2026-10-18 | Blender 5.2.2 LTS installed at `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe` (only real install; 4.2–5.0 folders are empty leftovers). Minimum supported stays 4.2; only 5.2 verifiable here | User-approved |
| 2026-10-18 | "Open in Blender" stays `blend`-only | Blender cannot open obj/fbx as a file argument |
| 2026-10-18 | Inline blend extraction emits only `asset.added`/`asset.updated` (already `ready`); `conversion.*` only for Blender jobs. `/convert` re-runs the same routing. Hover 3D viewport only when `has_proxy`. No migration: `001_init.sql` already has `has_thumbnail`, `has_proxy`, nullable `poly_count`/`proxy_poly_count` | architect gap decision |

| 2026-10-18 | Lowest verified Blender is 5.2.2 LTS; proxy script sets `media_type` only on ≥ 5.0; `.blend` opened with `use_scripts=False`; meta `blender_version` is `X.Y.Z` without the `LTS` suffix | Researcher findings on real 5.2 runs |
| 2026-10-18 | Embedded preview: REND → TEST → GLOB order, RGBA8 bottom-up, premultiplied alpha (un-premultiply for PNG), ≤ 512 px, ≤ 2 MiB decompressed read; big-endian / < 2.50 → no preview. zstd extraction only on Python ≥ 3.14, else Blender fallback | `docs/blend_header_spec.md` Part 2 |
| 2026-10-18 | Reverses the Phase 5 image-only rule (user decision): `.blend` shows its embedded preview instantly, then always runs a full Blender job (512px PNG + polycount + GLB), so `.blend` cards get the hover 3D viewer. Startup re-queues `.blend` assets that are `ready` without a proxy | User chose "Full 3D for .blend" after a code review flagged missing `.blend` proxies |
| 2026-10-18 | Proxy script switches any edit/sculpt/pose mode to object mode after load, and always frames a temporary 3/4 camera on all meshes (scene camera ignored) | Edit/sculpt/pose files failed `select_all.poll()`; workfile cameras often point away from the mesh |

## Backlog

- Maya conversion: out of scope for Phases 1–4 (not installed; launch-only per CLAUDE.md).
- Proxies for glb/gltf/stl/ply/usd/abc via Blender importers: later phase; obj/fbx cover the core convert feature.

- [for:backend-coder→frontend-coder] `POST /convert` without a file save keeps the same `v=<mtime>`, so browsers may show the stale obj/fbx thumbnail/proxy until next save. Fix only if it matters (e.g. expose a version id)

- [for:architect→backend-coder] If a library's `.cache` is a junction/symlink, the worker writes through it (`cache_dir()` + `os.replace`) and could overwrite a file outside the library. Serving already refuses this. Decide: refuse writes when `.cache` resolves outside the library (low severity: requires write access to the library)

## Log

- 2026-10-17 [orchestrator] Phase 0 done: CLAUDE.md, PLAN.md, 5 agents in `.claude/agents/`.
- 2026-10-17 [architect] Phase 1 architect items done: 4-phase plan, T1–T17, schema, OpenAPI spec, shell decision. Files: PLAN.md, CLAUDE.md, backend/migrations/001_init.sql, docs/api_spec.json. Verify: `python -c "import json;json.load(open('docs/api_spec.json'))"`
- 2026-10-17 [dcc-researcher] Phase 1 done (partial verify): proxy script written + py_compile only; header spec from real 4.2/4.3/4.4/5.0 headers, reference parser tested. Files: scripts/blender_export_proxy.py, docs/blend_header_spec.md. Blocked: Blender not installed.
- 2026-10-17 [frontend-coder] T7 done: Vite/React18/TS strict/Tailwind4 shell, typed client, useEvents WS hook, dev+preview proxy, vitest(jsdom) includes ../tests/frontend. Verify: npm --prefix frontend run build
- 2026-10-17 [frontend-coder] T8 done: library picker, ext+debounced search grid, live WS patching, per-card Open-in dropdown. Verify: npm --prefix frontend run build && npm --prefix frontend run test
- 2026-10-17 [frontend-coder] T9 done: useHoverPreview (2000 ms, single timer, AbortController, scroll/resize cancel). Verify: npm --prefix frontend run build
- 2026-10-17 [frontend-coder] T10 done: single lazy HoverViewport3D canvas, orbit, auto-frame, Wireframe/Clay, HUD, disposal + forceContextLoss, ?debug=1 hook. Not yet exercised in real WebGL (T15). Verify: npm --prefix frontend run build
- 2026-10-17 [backend-coder] T1–T6 done: db+migrations, header parser (all spec vectors), REST + envelope, scan/watchdog/WS, worker queue (timeout+tree-kill, 0 leftover procs on shutdown), detection + /launch. /apps empty here (no Blender). Files: backend/*.py, requirements.txt, .gitignore. Verify: .venv/Scripts/python -m backend.app; ruff clean
- 2026-10-17 [qa-tester] T11 blocked, T12/T13/T16 verified: 101 passed, 1 failed, 4 skipped (real-Blender tests). Failure: test_corrupt_compressed_stream_returns_none[zstd] (spec contradiction, see Backlog). Files: pytest.ini, tests/{conftest,fake_blender,test_blend_header,test_indexing,test_api,test_worker,test_blender_proxy}.py, tests/fixtures/make_fixtures.py. Verify: .venv/Scripts/python -m pytest -rs -q
- 2026-10-17 [qa-tester] T14 verified, T15 verified: vitest 14/14, Playwright 2/2 (system Chrome). Stress: 3 rounds x 10 previews, peak live WebGL contexts 1, 0 un-lost, 0 leaked buffers/asset textures, 12 fetches aborted mid-load; heap +2.28 MB over 3 rounds (flattening). Files: tests/frontend/{hover.test.tsx,dispose.test.ts}, tests/e2e/*. Verify: npm --prefix frontend run test; npx --prefix tests/e2e playwright test -c tests/e2e
- 2026-10-17 [orchestrator] Phase 3 re-verified independently: pytest 101 passed / 1 failed (T11 zstd) / 4 skipped; vitest 14/14; frontend build OK. Stopped before Phase 4 per user.
- 2026-10-18 [architect] Phase 5 planned: CLAUDE.md Tech Stack + Proxy script CLI + Preview rules; api_spec `v` param + has_thumbnail/has_proxy semantics; 10 Decisions; T18–T25; Backlog triaged (3 resolved, 1 added). No migration needed. Files: CLAUDE.md, PLAN.md, docs/api_spec.json. Verify: `python -c "import json;json.load(open('docs/api_spec.json'))"`
- 2026-10-18 [frontend-coder] T22 done: thumbnail/proxy URLs `?v=<mtime>`; three + @types/three pinned 0.182.0 (Clock deprecated in r183; R3F 8 always constructs Clock); 3D badge only when has_proxy. Verify: npm --prefix frontend run build && npm --prefix frontend run test (14/14)
- 2026-10-18 [dcc-researcher] T18 done: --input CLI (blend/obj/fbx), thumbnail-only mode, 11/11 cases pass on Blender 5.2.2 (full obj/fbx 4–6 s, thumb-only 3–3.5 s); spec Part 2 embedded preview with BHead layouts + tested reference, zstd contradiction fixed. Files: scripts/blender_export_proxy.py, docs/blend_header_spec.md. Verify: blender -b --factory-startup --python-exit-code 1 -P scripts/blender_export_proxy.py -- --input suzanne.fbx --out-png o.png --out-meta o.json --out-glb o.glb
- 2026-10-18 [backend-coder] T21 done: corrupt/undecodable compressed header → None; zstd partial dict only on ImportError. Verify: .venv/Scripts/python -m pytest -q tests/test_blend_header.py
- 2026-10-18 [backend-coder] T19 done: backend/blend_preview.py (flip, un-premultiply, bounds, 2 MiB). Verify: .venv/Scripts/python -m backend.blend_preview <file.blend> out.png → 128x128
- 2026-10-18 [backend-coder] T20 done: Worker.route by ext, new --input argv, /convert routing, v ignored, blend never has_proxy, legacy .glb cleanup on startup. Live on Blender 5.2: preview blend ready with 0 processes; NONE blend thumb-only ~5 s; obj/fbx full ~6 s. pytest -m "not blender": 92 passed / 10 intended failures (old argv/routing in tests → T23)
- 2026-10-18 [qa-tester] T23 done: fake Blender asserts new --input argv + thumbnail-only mode; routing/API cases updated; test_blend_preview.py covers all Part 2 vectors and bounds. Verify: .venv/Scripts/python -m pytest -q -m "not blender" (164 passed)
- 2026-10-18 [qa-tester] T24 done: make_fixtures.py builds 9 fixtures on Blender 5.2.2; 11 real-Blender tests incl. real daemon e2e (preview blend spawns 0 processes). Verify: .venv/Scripts/python -m pytest -q -rs -m blender (11 passed, 0 skipped)
- 2026-10-18 [qa-tester] T25 done: vitest 16/16, Playwright 3/3 x2 with fully clean console; blend cards never mount a canvas or request /proxy; stress: 1 context/preview, peak 1, 0 un-lost, 0 leaked buffers/asset textures. Verify: npm --prefix frontend run test; npx --prefix tests/e2e playwright test -c tests/e2e
- 2026-10-18 [orchestrator] T17 done: final re-run pytest 175 passed (0 skipped), vitest 16/16, build OK, ruff clean.
- 2026-10-18 [orchestrator] Review fixes: blend full 3D (worker.route always enqueues; PROXY_EXTS += blend; db.set_thumbnail; reroute image-only blends), proxy script object-mode + always-framed camera, tests updated (+ edit/sculpt real fixtures). Scroll alignment already handled by useHoverPreview (scroll/resize cancel). Verify: .venv/Scripts/python -m pytest -q (177 passed); npm --prefix frontend run test
- 2026-10-18 [orchestrator] Fix: hover preview stuck on "Loading…" for real (Draco) proxies. three 0.182 DRACOLoader has no default decoder path, so it fetched /draco_wasm_wrapper.js, got index.html and hung. Decoder now served from frontend/public/draco (setDecoderPath("/draco/")); parse has a 30 s timeout that shows an error instead of loading forever; e2e asserts the decoder is served. Verify: npx --prefix tests/e2e playwright test -c tests/e2e (4 passed)
