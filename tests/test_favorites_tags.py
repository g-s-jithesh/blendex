import os
import sqlite3

from conftest import add_library, assert_error, assert_shape, by_name, drain, items, settle, wait_until, ws_until

from backend import db


def tag_rows(path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT asset_id, tag FROM asset_tags ORDER BY asset_id, tag").fetchall()


def test_fresh_db_is_v2_with_favorites_and_tags():
    conn = db.connect(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "favorite" in {r[1] for r in conn.execute("PRAGMA table_info(assets)")}
    assert conn.execute("SELECT strict FROM pragma_table_list WHERE name = 'asset_tags'").fetchone() == (1,)
    assert "asset_tags_tag" in {r[1] for r in conn.execute("PRAGMA index_list(asset_tags)")}


def test_upgrades_existing_v1_db(tmp_path):
    path = str(tmp_path / "v1.sqlite")
    with sqlite3.connect(path) as old:
        old.executescript((db.MIGRATIONS / "001_init.sql").read_text(encoding="utf-8"))
        old.execute("INSERT INTO libraries (id, path, created_at) VALUES (1, '/l', 1)")
        old.execute("INSERT INTO assets (id, library_id, path, name, ext) VALUES (1, 1, '/l/a.obj', 'a', 'obj')")
        old.execute("INSERT INTO versions (id, asset_id, size, mtime, created_at) VALUES (1, 1, 1, 1, 1)")
        old.execute("UPDATE assets SET current_version_id = 1")
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert db.get_asset(conn, 1) | {"favorite": False, "tags": []} == db.get_asset(conn, 1)
    conn.close()
    assert db.connect(path).execute("PRAGMA user_version").fetchone()[0] == 2


def test_favorite_and_tags_endpoints_filters_and_validation(daemon, lib):
    for name in ("a.obj", "b.obj", "c.fbx"):
        (lib / name).write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a, b, cc = (by_name(c)[n] for n in ("a.obj", "b.obj", "c.fbx"))
        assert a["favorite"] is False and a["tags"] == []
        assert c.get("/api/tags").json() == []

        r = c.put(f"/api/assets/{a['id']}/favorite", json={"favorite": True})
        assert r.status_code == 200
        assert_shape(r.json(), "Asset")
        assert r.json() == c.get(f"/api/assets/{a['id']}").json() and r.json()["favorite"] is True

        r = c.put(f"/api/assets/{a['id']}/tags", json={"tags": ["  Rock ", "prop", "rock", "dark wood"]})
        assert r.status_code == 200 and r.json()["tags"] == ["dark wood", "prop", "rock"]
        c.put(f"/api/assets/{b['id']}/tags", json={"tags": ["rock"]})
        tags = c.get("/api/tags").json()
        assert_shape(tags, {"type": "array", "items": {"$ref": "TagCount"}})
        assert tags == [{"tag": "dark wood", "count": 1}, {"tag": "prop", "count": 1}, {"tag": "rock", "count": 2}]

        assert [x["name"] for x in items(c, favorite="true")] == ["a"]
        assert [x["name"] for x in items(c, favorite="false")] == ["b", "c"]
        assert [x["name"] for x in items(c, tag="rock")] == ["a", "b"]
        assert [x["name"] for x in items(c, tag="ROCK", favorite="true")] == ["a"]
        assert items(c, tag="ro") == []
        assert c.get("/api/assets", params={"tag": "rock", "limit": 1}).json()["total"] == 2

        c.put(f"/api/assets/{a['id']}/favorite", json={"favorite": False})
        assert items(c, favorite="true") == []
        c.put(f"/api/assets/{b['id']}/tags", json={"tags": []})
        assert c.get("/api/tags").json()[-1] == {"tag": "rock", "count": 1}

        for bad in ({"favorite": "yes"}, {"favorite": 1}, {}, {"favorite": None}):
            assert_error(c.put(f"/api/assets/{a['id']}/favorite", json=bad), 422, "VALIDATION_ERROR")
        for bad in (["  "], ["a,b"], ["line\nbreak"], ["x" * 41], [str(i) for i in range(21)], [5], "rock", None):
            assert_error(c.put(f"/api/assets/{a['id']}/tags", json={"tags": bad}), 422, "VALIDATION_ERROR")
        assert c.put(f"/api/assets/{cc['id']}/tags", json={"tags": ["x" * 40] + [str(i) for i in range(19)]}
                     ).status_code == 200
        assert c.get(f"/api/assets/{a['id']}").json()["tags"] == ["dark wood", "prop", "rock"]
        assert_error(c.put("/api/assets/999999/favorite", json={"favorite": True}), 404, "NOT_FOUND")
        assert_error(c.put("/api/assets/999999/tags", json={"tags": []}), 404, "NOT_FOUND")
        assert_error(c.get("/api/assets", params={"favorite": "maybe"}), 422, "VALIDATION_ERROR")
        drain(app, c)


def test_survive_file_edit_and_cascade_on_removal(daemon, lib, tmp_path):
    (lib / "a.obj").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
        c.put(f"/api/assets/{a['id']}/favorite", json={"favorite": True})
        c.put(f"/api/assets/{a['id']}/tags", json={"tags": ["hero"]})

        (lib / "a.obj").write_bytes(b"xy")
        os.utime(lib / "a.obj", (1_700_000_000, 1_700_000_000))
        wait_until(lambda: c.get(f"/api/assets/{a['id']}").json()["mtime"] == 1_700_000_000)
        settle(app, c)
        after = c.get(f"/api/assets/{a['id']}").json()
        assert after["size"] == 2 and after["favorite"] is True and after["tags"] == ["hero"]

        (lib / "a.obj").unlink()
        wait_until(lambda: items(c) == [])
        assert c.get("/api/tags").json() == []
        drain(app, c)
    assert tag_rows(tmp_path / "t.sqlite") == []


def test_edits_broadcast_full_asset(daemon, lib):
    (lib / "a.stl").write_bytes(b"x")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        a = items(c)[0]
        with c.websocket_connect("/ws/events") as ws:
            fav = c.put(f"/api/assets/{a['id']}/favorite", json={"favorite": True}).json()
            tagged = c.put(f"/api/assets/{a['id']}/tags", json={"tags": ["b", "a"]}).json()
            events = ws_until(ws, lambda ev: len(ev) >= 2)
        for e in events:
            assert_shape(e, "Event")
        assert [(e["type"], e["data"]) for e in events] == [("asset.updated", fav), ("asset.updated", tagged)]
        assert tagged["tags"] == ["a", "b"] and tagged["favorite"] is True
        drain(app, c)
