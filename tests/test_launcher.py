import importlib.util
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("webview")
spec = importlib.util.spec_from_file_location("blendex", Path(__file__).parent.parent / "packaging" / "blendex.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_claim_port_refuses_a_busy_port_and_takes_a_free_one(monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        monkeypatch.setattr(launcher, "PORT", busy.getsockname()[1])
        assert launcher.claim_port() is None
    sock = launcher.claim_port()
    assert sock is not None
    assert sock.getsockname() == ("127.0.0.1", launcher.PORT)
    sock.close()


def test_dam_port_overrides_the_port(monkeypatch):
    monkeypatch.setenv("DAM_PORT", "8799")
    spec.loader.exec_module(launcher)
    assert (launcher.PORT, launcher.URL) == (8799, "http://127.0.0.1:8799/")


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_uses_qt_the_xdg_data_dir_and_never_focuses_another_window(linux, tmp_path):
    assert linux.GUI == "qt"
    assert linux.WEBVIEW_DATA == tmp_path / "Blendex" / "webview"
    assert linux.focus_running_window() is False


def test_linux_message_tries_zenity_then_kdialog_then_stderr(linux, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, check: calls.append(cmd))
    for installed in ({"zenity", "kdialog"}, {"kdialog"}, set()):
        monkeypatch.setattr(shutil, "which", installed.__contains__)
        linux.message("a <b> & c", error=True)
    assert calls == [
        ["zenity", "--error", "--no-markup", "--title", "Blendex", "--text", "a <b> & c"],
        ["kdialog", "--title", "Blendex", "--error", "a <b> & c"],
    ]
    assert capsys.readouterr().err == "a <b> & c\n"
