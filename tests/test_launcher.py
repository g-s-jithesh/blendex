import importlib.util
import socket
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
