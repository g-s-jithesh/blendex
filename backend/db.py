import json
import os
import sqlite3
import time
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"
ASSET_SQL = (
    "SELECT a.id, a.library_id, a.path, a.name, a.ext, v.size, v.mtime, v.dcc_version, v.status,"
    " v.poly_count, v.error, v.has_thumbnail, v.has_proxy, a.favorite,"
    " (SELECT json_group_array(t.tag) FROM asset_tags t WHERE t.asset_id = a.id) AS tags"
    " FROM assets a JOIN versions v ON v.id = a.current_version_id"
)
UNDER = "(a.path = ? OR substr(a.path, 1, length(?)) = ?)"


def default_db_path() -> str:
    if env := os.environ.get("DAM_DB"):
        return env
    base = Path(os.environ["APPDATA"]) if os.name == "nt" else Path.home() / ".local" / "share"
    return str(base / "dam" / "dam.sqlite")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for f in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: int(p.name.split("_")[0])):
        n = int(f.name.split("_")[0])
        if n <= current:
            continue
        conn.executescript("BEGIN;\n" + f.read_text(encoding="utf-8") + "\nCOMMIT;")
        if conn.execute("PRAGMA user_version").fetchone()[0] != n:
            raise RuntimeError(f"migration {f.name} must set PRAGMA user_version = {n}")


def _dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _assets(cur: sqlite3.Cursor) -> list[dict]:
    rows = _dicts(cur)
    for d in rows:
        d["has_thumbnail"], d["has_proxy"] = bool(d["has_thumbnail"]), bool(d["has_proxy"])
        d["favorite"], d["tags"] = bool(d["favorite"]), sorted(json.loads(d["tags"]))
    return rows


def list_libraries(conn: sqlite3.Connection) -> list[dict]:
    return _dicts(conn.execute("SELECT id, path, created_at FROM libraries ORDER BY id"))


def get_library(conn: sqlite3.Connection, library_id: int) -> dict | None:
    rows = _dicts(conn.execute("SELECT id, path, created_at FROM libraries WHERE id = ?", (library_id,)))
    return rows[0] if rows else None


def add_library(conn: sqlite3.Connection, path: str) -> dict:
    cur = conn.execute("INSERT INTO libraries (path, created_at) VALUES (?, ?)", (path, int(time.time())))
    return get_library(conn, cur.lastrowid)


def delete_library(conn: sqlite3.Connection, library_id: int) -> None:
    conn.execute("DELETE FROM libraries WHERE id = ?", (library_id,))


def get_asset(conn: sqlite3.Connection, asset_id: int) -> dict | None:
    rows = _assets(conn.execute(ASSET_SQL + " WHERE a.id = ?", (asset_id,)))
    return rows[0] if rows else None


def list_assets(conn: sqlite3.Connection, library_id: int | None = None, q: str | None = None,
                ext: str | None = None, limit: int = 100, offset: int = 0, favorite: bool | None = None,
                tag: str | None = None) -> tuple[list[dict], int]:
    where = (
        " WHERE (? IS NULL OR a.library_id = ?) AND (? IS NULL OR instr(lower(a.name), lower(?)) > 0)"
        " AND (? IS NULL OR a.ext = lower(?)) AND (? IS NULL OR a.favorite = ?)"
        " AND (? IS NULL OR EXISTS (SELECT 1 FROM asset_tags t WHERE t.asset_id = a.id AND t.tag = ?))"
    )
    fav = None if favorite is None else int(favorite)
    args = (library_id, library_id, q, q, ext, ext, fav, fav, tag, tag)
    rows = conn.execute(ASSET_SQL + where + " ORDER BY a.name, a.id LIMIT ? OFFSET ?", (*args, limit, offset))
    total = conn.execute(
        "SELECT count(*) FROM assets a JOIN versions v ON v.id = a.current_version_id" + where, args
    ).fetchone()[0]
    return _assets(rows), total


def set_favorite(conn: sqlite3.Connection, asset_id: int, favorite: bool) -> None:
    conn.execute("UPDATE assets SET favorite = ? WHERE id = ?", (int(favorite), asset_id))


def set_tags(conn: sqlite3.Connection, asset_id: int, tags: list[str]) -> None:
    conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
    conn.executemany("INSERT INTO asset_tags (asset_id, tag) VALUES (?, ?)", [(asset_id, t) for t in tags])


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    return _dicts(conn.execute("SELECT tag, count(*) AS count FROM asset_tags GROUP BY tag ORDER BY tag"))


def current_version_id(conn: sqlite3.Connection, asset_id: int) -> int | None:
    row = conn.execute("SELECT current_version_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row[0] if row else None


def _under_args(under: str) -> tuple[str, str, str]:
    prefix = under if under.endswith("/") else under + "/"
    return (under, prefix, prefix)


def snapshot(conn: sqlite3.Connection, library_id: int, under: str) -> dict[str, tuple[int, int]]:
    rows = conn.execute(
        "SELECT a.path, v.size, v.mtime FROM assets a LEFT JOIN versions v ON v.id = a.current_version_id"
        " WHERE a.library_id = ? AND " + UNDER,
        (library_id, *_under_args(under)),
    )
    return {path: (size, mtime) for path, size, mtime in rows}


def upsert_asset(conn: sqlite3.Connection, library_id: int, path: str, size: int, mtime: int,
                 dcc_version: str | None, status: str) -> tuple[int, bool] | None:
    row = conn.execute(
        "SELECT a.id, v.size, v.mtime FROM assets a LEFT JOIN versions v ON v.id = a.current_version_id"
        " WHERE a.path = ?",
        (path,),
    ).fetchone()
    if row and row[1:] == (size, mtime):
        return None
    p = Path(path)
    asset_id = row[0] if row else conn.execute(
        "INSERT INTO assets (library_id, path, name, ext) VALUES (?, ?, ?, ?)",
        (library_id, path, p.stem, p.suffix[1:].lower()),
    ).lastrowid
    vid = conn.execute(
        "INSERT INTO versions (asset_id, size, mtime, dcc_version, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (asset_id, size, mtime, dcc_version, status, int(time.time())),
    ).lastrowid
    conn.execute("UPDATE assets SET current_version_id = ? WHERE id = ?", (vid, asset_id))
    return asset_id, row is None


def remove_assets(conn: sqlite3.Connection, under: str) -> list[dict]:
    args = _under_args(under)
    gone = _assets(conn.execute(ASSET_SQL + " WHERE " + UNDER, args))
    conn.execute("DELETE FROM assets AS a WHERE " + UNDER, args)
    return gone


def reroute_ids(conn: sqlite3.Connection, library_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT a.id FROM assets a JOIN versions v ON v.id = a.current_version_id WHERE a.library_id = ?"
        " AND (v.status IN ('pending', 'converting') OR (a.ext = 'blend' AND v.status = 'ready' AND v.has_proxy = 0))",
        (library_id,),
    )
    return [r[0] for r in rows]


def pend_unsupported(conn: sqlite3.Connection, library_id: int, exts: set[str]) -> None:
    conn.execute(
        "UPDATE versions SET status = 'pending', error = NULL WHERE status = 'unsupported' AND id IN"
        " (SELECT current_version_id FROM assets WHERE library_id = ? AND ext IN (SELECT value FROM json_each(?)))",
        (library_id, json.dumps(sorted(exts))),
    )


def set_status(conn: sqlite3.Connection, version_id: int, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE versions SET status = ?, error = ? WHERE id = ?", (status, error, version_id))


def set_result(conn: sqlite3.Connection, version_id: int, poly_count: int | None, proxy_poly_count: int | None,
               has_proxy: bool) -> None:
    conn.execute(
        "UPDATE versions SET status = 'ready', error = NULL, poly_count = ?, proxy_poly_count = ?,"
        " has_thumbnail = 1, has_proxy = ? WHERE id = ?",
        (poly_count, proxy_poly_count, int(has_proxy), version_id),
    )


def set_thumbnail(conn: sqlite3.Connection, version_id: int) -> None:
    conn.execute("UPDATE versions SET has_thumbnail = 1 WHERE id = ?", (version_id,))


def clear_cache_flags(conn: sqlite3.Connection, version_id: int) -> None:
    conn.execute(
        "UPDATE versions SET status = 'pending', has_thumbnail = 0, has_proxy = 0 WHERE id = ?", (version_id,)
    )


def replace_apps(conn: sqlite3.Connection, apps: list[dict]) -> None:
    conn.execute("DELETE FROM dcc_apps")
    conn.executemany(
        "INSERT INTO dcc_apps (id, name, version, executable, exts) VALUES (?, ?, ?, ?, ?)",
        [(a["id"], a["name"], a["version"], a["executable"], json.dumps(a["exts"])) for a in apps],
    )


def list_apps(conn: sqlite3.Connection) -> list[dict]:
    rows = _dicts(conn.execute("SELECT id, name, version, executable, exts FROM dcc_apps ORDER BY id"))
    return [r | {"exts": json.loads(r["exts"])} for r in rows]


def get_app(conn: sqlite3.Connection, app_id: str) -> dict | None:
    return next((a for a in list_apps(conn) if a["id"] == app_id), None)


def list_custom_apps(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT executable FROM custom_apps ORDER BY rowid DESC")]


def add_custom_app(conn: sqlite3.Connection, executable: str) -> None:
    conn.execute("INSERT OR REPLACE INTO custom_apps (executable) VALUES (?)", (executable,))


def repend_missing_app(conn: sqlite3.Connection, prefix: str) -> list[int]:
    rows = conn.execute(
        "UPDATE versions SET status = 'pending', error = NULL WHERE status = 'failed'"
        " AND substr(error, 1, length(?)) = ? AND id IN (SELECT current_version_id FROM assets)"
        " RETURNING (SELECT id FROM assets WHERE current_version_id = versions.id)",
        (prefix, prefix),
    )
    return [r[0] for r in rows]
