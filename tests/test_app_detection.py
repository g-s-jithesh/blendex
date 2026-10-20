import logging
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import add_library, assert_error, assert_shape, drain, wait_until, ws_until

from backend import dcc
from backend.worker import BLENDER_MISSING

EXE = ".exe" if sys.platform == "win32" else ""
HKCR, HKCU, HKLM = "HKCR", "HKCU", "HKLM"


def write(path: Path, data: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class Key:
    def __init__(self, root: str, key: str):
        self.root, self.key = root, key.lower()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeRegistry:
    HKEY_CLASSES_ROOT, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = HKCR, HKCU, HKLM

    def __init__(self, values: dict[tuple[str, str], dict[str, str]]):
        self.keys = {(root, key.lower()): v for (root, key), v in values.items()}

    def _sub(self, h: Key) -> list[str]:
        prefix = h.key + "\\"
        return sorted({k[len(prefix):].split("\\")[0] for r, k in self.keys if r == h.root and k.startswith(prefix)})

    def OpenKey(self, root, key):
        h = Key(root, key)
        if (root, h.key) not in self.keys and not self._sub(h):
            raise FileNotFoundError(key)
        return h

    def QueryValueEx(self, h, name):
        try:
            return self.keys[(h.root, h.key)][name], 1
        except KeyError:
            raise FileNotFoundError(name) from None

    def QueryInfoKey(self, h):
        return len(self._sub(h)), len(self.keys.get((h.root, h.key), {})), 0

    def EnumKey(self, h, i):
        return self._sub(h)[i]

    def EnumValue(self, h, i):
        return list(self.keys[(h.root, h.key)])[i], "", 1


@pytest.fixture
def fake_windows(tmp_path, monkeypatch):
    def install(values, versions=None):
        monkeypatch.setattr(dcc, "winreg", FakeRegistry(values))
        monkeypatch.setattr(dcc, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
        monkeypatch.setitem(dcc.CANDIDATES, "win32", [])
        monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which=lambda _: None))
        monkeypatch.setattr(dcc, "_fixed_drives", lambda: [f"{tmp_path.as_posix()}/D/"])
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        monkeypatch.setattr(dcc.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        calls = []

        def cli(exe):
            calls.append(Path(exe).resolve())
            return (versions or {}).get(Path(exe).parent.name)

        monkeypatch.setattr(dcc, "cli_version", cli)
        return calls

    return install


UNINSTALL, WOW = dcc.UNINSTALL


def test_windows_sources_find_every_install(tmp_path, fake_windows, caplog):
    t = tmp_path
    pf = write(t / "D" / "Program Files (x86)" / "Blender Foundation" / "Blender 4.4" / "blender.exe")
    steam = write(t / "games" / "steamapps" / "common" / "Blender" / "blender.exe")
    libs = [str(t / "steam").replace("\\", "\\\\"), str(t / "games").replace("\\", "\\\\")]
    entries = "".join(f' "{i}"\n {{\n  "path"\t\t"{lib}"\n }}\n' for i, lib in enumerate(libs))
    write(t / "steam" / "steamapps" / "libraryfolders.vdf", f'"libraryfolders"\n{{\n{entries}}}\n'.encode())
    assoc = write(t / "assoc" / "blender.exe")
    write(t / "assoc" / "blender-launcher.exe")
    choice = write(t / "portable dir" / "blender.exe")
    uninstall = write(t / "uninst" / "blender.exe")
    maya_icon = write(t / "Maya2026" / "bin" / "maya.exe")
    maya_reg = write(t / "CustomMaya" / "bin" / "maya.exe")
    store = write(t / "local" / "Microsoft" / "WindowsApps" / "blender.exe")
    scoop = write(t / "home" / "scoop" / "apps" / "blender" / "current" / "blender.exe")
    calls = fake_windows({
        (HKCU, r"Software\Valve\Steam"): {"SteamPath": (t / "steam").as_posix()},
        (HKCR, ".blend"): {"": "blender.4.9"},
        (HKCR, r"blender.4.9\shell\open\command"): {"": f'"{t / "assoc" / "blender-launcher.exe"}" "%1"'},
        (HKCU, dcc.FILE_EXTS + r"\UserChoice"): {"ProgId": r"Applications\blender.exe"},
        (HKCU, r"Software\Classes\Applications\blender.exe\shell\open\command"): {"": f"{choice} %1"},
        (HKLM, UNINSTALL + r"\{B}"): {"DisplayName": "Blender", "InstallLocation": str(t / "uninst") + os.sep},
        (HKLM, UNINSTALL + r"\{N}"): {"DisplayName": "Notepad++", "InstallLocation": str(t / "assoc")},
        (HKCU, WOW + r"\{M}"): {"DisplayName": "Autodesk Maya 2026", "DisplayIcon": f'"{maya_icon}",0'},
        (HKLM, dcc.MAYA_KEY + r"\2025\Setup\InstallPath"): {"MAYA_INSTALL_LOCATION": str(t / "CustomMaya") + os.sep},
        (HKLM, dcc.MAYA_KEY + r"\Capabilities"): {},
    }, {"Blender": "4.1", "assoc": "4.9", "portable dir": "4.3", "uninst": "4.5", "WindowsApps": "4.6",
        "current": "4.7"})
    with caplog.at_level(logging.INFO, logger="backend.dcc"):
        apps = dcc.detect_apps()
    got = {a["id"]: a["executable"] for a in apps}
    assert got == {
        "blender-4.4": pf.resolve().as_posix(), "maya-2026": maya_icon.resolve().as_posix(),
        "blender-4.1": steam.resolve().as_posix(), "blender-4.9": assoc.resolve().as_posix(),
        "blender-4.3": choice.resolve().as_posix(), "blender-4.5": uninstall.resolve().as_posix(),
        "maya-2025": maya_reg.resolve().as_posix(), "blender-4.6": store.resolve().as_posix(),
        "blender-4.7": scoop.resolve().as_posix(),
        "explorer": (dcc.SYSTEM32.parent / "explorer.exe").as_posix(),
        "open-with": (dcc.SYSTEM32 / "rundll32.exe").as_posix(),
    }
    assert pf.resolve() not in calls
    assert f"found blender-4.1 at {steam.resolve().as_posix()} (source: Steam)" in caplog.messages
    assert f"found maya-2025 at {maya_reg.resolve().as_posix()} (source: Maya registry)" in caplog.messages
    assert f"found blender-4.9 at {assoc.resolve().as_posix()} (source: .blend association)" in caplog.messages


def test_failing_sources_are_logged_and_skipped(tmp_path, fake_windows, caplog):
    fake_windows({(HKCU, r"Software\Valve\Steam"): {"SteamPath": str(tmp_path / "nosteam")}})
    with caplog.at_level(logging.INFO, logger="backend.dcc"):
        apps = dcc.detect_apps()
    assert [a["id"] for a in apps] == ["explorer", "open-with"]
    assert any(m.startswith("app source Steam failed: FileNotFoundError") for m in caplog.messages)
    assert "no Blender found" in caplog.messages


def test_no_winreg_off_windows_still_lists_nothing(monkeypatch):
    monkeypatch.setattr(dcc, "winreg", None)
    monkeypatch.setattr(dcc, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(dcc, "_fixed_drives", list)
    monkeypatch.setattr(dcc, "shutil", SimpleNamespace(which=lambda _: None))
    monkeypatch.setitem(dcc.CANDIDATES, "win32", [])
    assert [a["id"] for a in dcc.detect_apps()] == ["explorer", "open-with"]


@pytest.mark.skipif(sys.platform != "win32", reason="real registry")
def test_real_windows_sources_never_raise():
    for _name, find in dcc.windows_sources():
        assert isinstance(find(), list)


@pytest.fixture
def no_cli(monkeypatch):
    versions: dict[str, str] = {}
    monkeypatch.setattr(dcc, "cli_version", lambda exe: versions.get(Path(exe).parent.name))
    return versions


def test_add_app_by_folder_or_file_is_idempotent_and_persists(daemon, tmp_path, no_cli):
    exe = write(tmp_path / "Portable" / f"blender{EXE}")
    no_cli["Portable"] = "4.3"
    with daemon() as (_app, c):
        r = c.post("/api/apps", json={"executable": str(exe.parent)})
        assert r.status_code == 201, r.text
        assert_shape(r.json(), {"$ref": "App"})
        want = {"id": "blender-4.3", "name": "Blender 4.3", "version": "4.3", "executable": exe.resolve().as_posix(),
                "exts": ["blend", *dcc.BLENDER_IMPORTS]}
        assert r.json() == want
        again = c.post("/api/apps", json={"executable": f'"{exe}"'})
        assert (again.status_code, again.json()) == (201, want)
        assert want in c.get("/api/apps").json()
    with sqlite3.connect(tmp_path / "t.sqlite") as conn:
        assert conn.execute("SELECT executable FROM custom_apps").fetchall() == [(want["executable"],)]
    with daemon() as (_app, c):
        assert want in c.get("/api/apps").json()


def test_add_maya_folder_and_blender_launcher(daemon, tmp_path, no_cli):
    maya = write(tmp_path / "Maya2026" / "bin" / f"maya{EXE}")
    blender = write(tmp_path / "B" / f"blender{EXE}")
    launcher = write(tmp_path / "B" / "blender-launcher.exe")
    no_cli["B"] = "5.1"
    with daemon() as (_app, c):
        r = c.post("/api/apps", json={"executable": str(maya.parent.parent)})
        assert (r.status_code, r.json()["id"], r.json()["executable"]) == (201, "maya-2026", maya.resolve().as_posix())
        r = c.post("/api/apps", json={"executable": str(launcher)})
        assert (r.status_code, r.json()["executable"]) == (201, blender.resolve().as_posix())


def test_add_app_rejects_bad_paths(daemon, tmp_path, no_cli):
    write(tmp_path / "Bad" / f"blender{EXE}")
    notepad = write(tmp_path / "N" / f"notepad{EXE}")
    mayapy = write(tmp_path / "M" / "bin" / f"mayapy{EXE}")
    empty = tmp_path / "empty"
    empty.mkdir()
    with daemon() as (_app, c):
        for bad in (str(tmp_path / "missing" / "blender.exe"), str(empty), str(notepad), str(mayapy),
                    str(tmp_path / "Bad"), "Portable/blender.exe"):
            r = c.post("/api/apps", json={"executable": bad})
            assert_error(r, 422, "INVALID_PATH")
            assert r.json()["error"]["message"]
        assert "did not report a Blender version" in c.post("/api/apps", json={"executable": str(tmp_path / "Bad")}
                                                            ).json()["error"]["message"]
        assert_error(c.post("/api/apps", json={"executable": ""}), 422, "VALIDATION_ERROR")
        assert_error(c.post("/api/apps", json={}), 422, "VALIDATION_ERROR")
        if sys.platform == "win32":
            assert_error(c.post("/api/apps", json={"executable": str(write(tmp_path / "X" / "blender"))}),
                         422, "INVALID_PATH")
        assert [a["id"] for a in c.get("/api/apps").json() if a["id"].startswith(("blender", "maya"))] == []


def test_rescan_finds_new_installs_and_repends_missing_blender_failures(daemon, lib, tmp_path, monkeypatch):
    monkeypatch.delenv("DAM_BLENDER")
    platform = sys.platform if sys.platform in dcc.CANDIDATES else "linux"
    monkeypatch.setitem(dcc.CANDIDATES, platform, [str(tmp_path / "PF" / "Blender *" / f"blender{EXE}")])
    write(lib / "m.obj", b"o")
    with daemon() as (app, c):
        add_library(c, lib)
        drain(app, c)
        asset = c.get("/api/assets").json()["items"][0]
        assert (asset["status"], asset["error"]) == ("failed", BLENDER_MISSING)
        exe = write(tmp_path / "PF" / "Blender 5.3" / f"blender{EXE}")
        with c.websocket_connect("/ws/events") as ws:
            r = c.post("/api/apps/rescan")
            assert r.status_code == 200
            assert_shape(r.json(), {"type": "array", "items": {"$ref": "App"}})
            assert {"blender-5.3": exe.resolve().as_posix()}.items() <= {a["id"]: a["executable"]
                                                                         for a in r.json()}.items()
            events = ws_until(ws, lambda seen: any(e["type"] == "asset.updated" for e in seen))
        updated = next(e for e in events if e["type"] == "asset.updated")
        assert (updated["asset_id"], updated["data"]["status"], updated["data"]["error"]) == (
            asset["id"], "pending", None)
        wait_until(lambda: c.get(f"/api/assets/{asset['id']}").json()["status"] == "pending")
        assert r.json() == c.get("/api/apps").json()
