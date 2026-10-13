"""Mode switch legacy/neu: read from the nginx symlink, switch by writing a wish file.

The container never switches itself (that needs root on the host); it only reports the link and
writes ``legacy`` or ``neu`` for a host path unit. Without configured paths (printer-int) there
is nothing to switch.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "test-token-not-a-real-secret"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    import api
    import app as kiosk

    tokens = tmp_path / "api_tokens.txt"
    tokens.write_text("test:%s\n" % hashlib.sha256(TOKEN.encode()).hexdigest())
    monkeypatch.setattr(api, "token_file", lambda: str(tokens))
    for key in ("PRINTER_MODE_LINK", "PRINTER_MODE_WISH"):
        monkeypatch.delenv(key, raising=False)
    kiosk.app.config.update(TESTING=True)
    if kiosk.limiter is not None:
        kiosk.limiter.reset()
    return kiosk.app.test_client()


@pytest.fixture
def pi(tmp_path, monkeypatch):
    """The Pi layout: a symlink like /etc/nginx/printer/active.conf and a wish directory."""
    nginx = tmp_path / "nginx"
    nginx.mkdir()
    link = nginx / "active.conf"
    os.symlink("upstream-legacy.conf", link)  # target need not exist, exactly like a ro mount
    wunsch_dir = tmp_path / "printer-mode"
    wunsch_dir.mkdir()
    monkeypatch.setenv("PRINTER_MODE_LINK", str(link))
    monkeypatch.setenv("PRINTER_MODE_WISH", str(wunsch_dir / "wunsch"))
    return link, wunsch_dir / "wunsch"


def test_modus_wird_aus_dem_link_gelesen(umgebung, pi):
    link, _ = pi
    assert umgebung.get("/api/v1/modus", headers=AUTH).get_json() == {"modus": "legacy", "umschaltbar": True}
    os.remove(link)
    os.symlink("/etc/nginx/printer/upstream-neu.conf", link)
    assert umgebung.get("/api/v1/modus", headers=AUTH).get_json()["modus"] == "neu"


def test_umschalten_schreibt_genau_den_wunsch(umgebung, pi):
    _, wunsch = pi
    antwort = umgebung.post("/api/v1/modus", json={"modus": "neu"}, headers=AUTH)
    assert antwort.status_code == 202
    assert wunsch.read_text() == "neu"
    # No temp file left behind for the path unit to trip over.
    assert sorted(p.name for p in wunsch.parent.iterdir()) == ["wunsch"]
    assert umgebung.post("/api/v1/modus", json={"modus": "legacy"}, headers=AUTH).status_code == 202
    assert wunsch.read_text() == "legacy"


def test_ungueltiger_modus_400_und_nichts_geschrieben(umgebung, pi):
    _, wunsch = pi
    for body in ({"modus": "../etc"}, {"modus": "LEGACY"}, {}, {"modus": ["neu"]}):
        assert umgebung.post("/api/v1/modus", json=body, headers=AUTH).status_code == 400
    assert not wunsch.exists()


def test_ohne_token_401_und_nichts_geschrieben(umgebung, pi):
    _, wunsch = pi
    assert umgebung.get("/api/v1/modus").status_code == 401
    falsch = {"Authorization": "Bearer falsch"}
    assert umgebung.post("/api/v1/modus", json={"modus": "neu"}, headers=falsch).status_code == 401
    assert not wunsch.exists()


def test_ohne_pfade_nicht_umschaltbar(umgebung):
    """printer-int: no link, no wish directory."""
    assert umgebung.get("/api/v1/modus", headers=AUTH).get_json() == {"modus": "neu", "umschaltbar": False}
    assert umgebung.post("/api/v1/modus", json={"modus": "legacy"}, headers=AUTH).status_code == 409


def test_wunschverzeichnis_fehlt_409(umgebung, pi, monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_MODE_WISH", str(tmp_path / "gibt-es-nicht" / "wunsch"))
    assert umgebung.get("/api/v1/modus", headers=AUTH).get_json()["umschaltbar"] is False
    assert umgebung.post("/api/v1/modus", json={"modus": "neu"}, headers=AUTH).status_code == 409


def test_unlesbarer_link_modus_unbekannt(umgebung, pi):
    link, _ = pi
    os.remove(link)
    os.symlink("etwas-anderes.conf", link)
    assert umgebung.get("/api/v1/modus", headers=AUTH).get_json()["modus"] is None
