import gzip
import os
import shutil
import sqlite3
import time
from contextlib import closing

import pytest
from compression import zstd
from conftest import add_library, blend, by_name, drain, items, preview_blend, spawned, wait_until

from backend import db

TABLES = {"libraries", "assets", "versions", "dcc_apps", "asset_tags"}


def write(path, data: bytes, mtime: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def query(dbfile, sql: str, *args) -> list[tuple]:
    with closing(db.connect(str(dbfile))) as conn:
        return conn.execute(sql, args).fetchall()


def versions(dbfile, asset_id: int) -> list[tuple]:
    return query(dbfile, "SELECT id, size, mtime, dcc_version FROM versions WHERE asset_id = ? ORDER BY id", asset_id)


def current_vid(dbfile, asset_id: int) -> int:
    return query(dbfile, "SELECT current_version_id FROM assets WHERE id = ?", asset_id)[0][0]


def test_migration_user_version_strict_and_pragmas(tmp_path):
    path = str(tmp_path / "m.sqlite")
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone() == (2,)
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert conn.execute("PRAGMA busy_timeout").fetchone() == (5000,)
    strict = dict(conn.execute("SELECT name, strict FROM pragma_table_list WHERE schema = 'main'").fetchall())
    assert {t: strict.get(t) for t in TABLES} == dict.fromkeys(TABLES, 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO libraries (path, created_at) VALUES ('x', 'not-an-int')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dcc_apps (id, name, executable, exts) VALUES ('a', 'A', 'a.exe', 'not json')")
    conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone() == (2,)
    conn.close()


def test_foreign_keys_cascade_in_db(tmp_path):
    conn = db.connect(str(tmp_path / "c.sqlite"))
    with conn:
        lib = db.add_library(conn, "C:/lib")
        _aid, created = db.upsert_asset(conn, lib["id"], "C:/lib/a.blend", 10, 100, "4.2", "pending")
        db.upsert_asset(conn, lib["id"], "C:/lib/a.blend", 11, 101, "4.3", "pending")
    assert created and conn.execute("SELECT count(*) FROM versions").fetchone() == (2,)
    with conn:
        db.delete_library(conn, lib["id"])
    assert conn.execute("SELECT count(*) FROM assets").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM versions").fetchone() == (0,)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO assets (library_id, path, name, ext) VALUES (999, 'p', 'n', 'blend')")
    conn.close()


IGNORED = [".cache/999.glb", ".cache/x.blend", "x.blend1", "x.blend@", "notes.txt", ".hidden/h.blend",
           "~tmp/t.blend", ".dot.blend", "~lock.blend", "sub/.git/z.blend", "sub/~bak/y.obj"]


def test_initial_scan_inserts_rows(daemon, lib, spawn_log):
    write(lib / "a.blend", blend("4.2"))
    write(lib / "p.blend", preview_blend())
    write(lib / "f.stl", b"solid")
    write(lib / "Upper.BLEND", blend("2.90"))
    write(lib / "sub" / "deep" / "c.blend", zstd.compress(blend("5.0")))
    write(lib / "g.blend", gzip.compress(blend("3.6")))
    write(lib / "junk.blend", b"not a blend file")
    write(lib / "empty.blend", b"")
    write(lib / "d.obj", b"o 1")
    write(lib / "e.fbx", b"fbx")
    for rel in IGNORED:
        write(lib / rel, blend("4.2"))
    with daemon() as (app, c):
        library = add_library(c, lib)
        drain(app, c)
        got = by_name(c)
    assert set(got) == {"a.blend", "Upper.blend", "c.blend", "g.blend", "junk.blend", "empty.blend", "d.obj",
                        "e.fbx", "p.blend", "f.stl"}
    assert {k: v["dcc_version"] for k, v in got.items()} == {
        "a.blend": "4.2", "Upper.blend": "2.90", "c.blend": "5.0", "g.blend": "3.6", "junk.blend": None,
        "empty.blend": None, "d.obj": None, "e.fbx": None, "p.blend": "5.2", "f.stl": None,
    }
    assert got["c.blend"]["path"] == (lib / "sub" / "deep" / "c.blend").as_posix()
    for a in got.values():
        assert a["library_id"] == library["id"]
        assert "\\" not in a["path"] and os.path.isabs(a["path"])
        st = os.stat(a["path"])
        assert (a["size"], a["mtime"]) == (st.st_size, int(st.st_mtime))
    assert sorted(spawned(spawn_log)) == sorted(
        [(n, "full") for n in ("a.blend", "Upper.BLEND", "c.blend", "g.blend", "junk.blend", "empty.blend",
                               "p.blend")]
        + [("d.obj", "full"), ("e.fbx", "full")])
    flags = {k: (v["status"], v["has_thumbnail"], v["has_proxy"]) for k, v in got.items()}
    assert flags == {k: ("unsupported", False, False) if k == "f.stl" else ("ready", True, True)
                     for k in got}
    assert got["p.blend"]["poly_count"] == got["a.blend"]["poly_count"] == 1234
    for k, v in got.items():
        assert (lib / ".cache" / f"{v['id']}.glb").exists() == v["has_proxy"], k


def test_size_mtime_change_adds_version_and_moves_current(daemon, lib, tmp_path):
    dbfile = tmp_path / "t.sqlite"
    t0 = int(time.time()) - 1000
    f = write(lib / "a.blend", blend("4.2"), t0)
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        aid = items(c)[0]["id"]
        v1 = current_vid(dbfile, aid)
        write(f, blend("5.0", b"more"), t0 + 10)
        wait_until(lambda: c.get(f"/api/assets/{aid}").json()["dcc_version"] == "5.0")
        drain(app, c)
        v2 = current_vid(dbfile, aid)
        assert v2 != v1 and [r[0] for r in versions(dbfile, aid)] == [v1, v2]
        assert c.get(f"/api/assets/{aid}").json()["size"] == f.stat().st_size

        write(f, blend("5.0", b"more"), t0 + 20)
        wait_until(lambda: len(versions(dbfile, aid)) == 3)
        os.utime(f, (t0 + 20, t0 + 20))
        drain(app, c)
        time.sleep(1)
        assert len(versions(dbfile, aid)) == 3
    write(f, blend("4.4", b"offline edit!"), t0 + 30)
    with daemon() as (app, c):
        drain(app, c)
        assert c.get(f"/api/assets/{aid}").json()["dcc_version"] == "4.4"
    rows = versions(dbfile, aid)
    assert len(rows) == 4 and rows[-1][0] == current_vid(dbfile, aid) and rows[-1][2] == t0 + 30


def test_watcher_ignores_cache_and_hidden_paths(daemon, lib):
    write(lib / "a.blend", blend("4.2"))
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        aid = items(c)[0]["id"]
        assert (lib / ".cache" / f"{aid}.png").is_file() and (lib / ".cache" / f"{aid}.glb").is_file()
        for rel in IGNORED:
            write(lib / rel, blend("4.2"))
        write(lib / "zz_marker.obj", b"o")
        wait_until(lambda: "zz_marker.obj" in by_name(c))
        drain(app, c)
        time.sleep(1)
        assert set(by_name(c)) == {"a.blend", "zz_marker.obj"}


def test_watcher_add_modify_rename_delete(daemon, lib):
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        assert items(c) == []
        f = write(lib / "sub" / "new.blend", blend("4.3"))
        a = wait_until(lambda: by_name(c).get("new.blend"))
        assert a["dcc_version"] == "4.3"
        write(f, blend("4.4", b"grown"), int(time.time()) + 5)
        wait_until(lambda: c.get(f"/api/assets/{a['id']}").json()["dcc_version"] == "4.4")
        f.rename(lib / "sub" / "renamed.blend")
        wait_until(lambda: set(by_name(c)) == {"renamed.blend"})
        (lib / "sub" / "renamed.blend").unlink()
        wait_until(lambda: items(c) == [])
        write(lib / "d1" / "x.obj", b"o")
        write(lib / "d1" / "y.obj", b"o")
        wait_until(lambda: len(items(c)) == 2)
        shutil.rmtree(lib / "d1")
        wait_until(lambda: items(c) == [])
        drain(app, c)


def test_library_delete_cascades_without_touching_files(daemon, lib, tmp_path):
    files = {lib / "a.blend": blend("4.2"), lib / "s" / "b.obj": b"obj", lib / "c.fbx": b"fbx"}
    for p, data in files.items():
        write(p, data)
    with daemon() as (app, c):
        library = add_library(c, lib)
        drain(app, c)
        assert len(items(c)) == 3
        assert c.delete(f"/api/libraries/{library['id']}").status_code == 204
        assert items(c) == [] and c.get("/api/libraries").json() == []
        assert c.delete(f"/api/libraries/{library['id']}").status_code == 404
        write(lib / "after.blend", blend("4.2"))
        time.sleep(1)
        drain(app, c)
        assert items(c) == []
    for t in ("libraries", "assets", "versions"):
        assert query(tmp_path / "t.sqlite", f"SELECT count(*) FROM {t}") == [(0,)]
    for p, data in files.items():
        assert p.read_bytes() == data
