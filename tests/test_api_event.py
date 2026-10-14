"""POST /api/v1/event (Karte 1539): sets the kiosk event globally, only with the API token.

GET /?event= stays page-local (test_kiosk_event.py); this is the authenticated way to change it
for every device.
"""

import hashlib
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "test-token-not-a-real-secret"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_SIM", "1")
    monkeypatch.chdir(tmp_path)
    import app as kiosk
    importlib.reload(kiosk)
    import api
    tokens = tmp_path / "api_tokens.txt"
    tokens.write_text("test:%s\n" % hashlib.sha256(TOKEN.encode()).hexdigest())
    monkeypatch.setattr(api, "token_file", lambda: str(tokens))
    monkeypatch.setattr(kiosk, "RUNTIME_FILE", str(tmp_path / "runtime.txt"))
    kiosk.app.config["TESTING"] = True
    if kiosk.limiter is not None:
        kiosk.limiter.reset()
    return kiosk.app.test_client()


def test_setzt_den_anlass_global(client):
    import app as kiosk
    antwort = client.post("/api/v1/event", json={"event": "  Guild42 Meetup Oktober "}, headers=AUTH)
    assert antwort.status_code == 200
    assert antwort.get_json() == {"ok": True, "active": "Guild42 Meetup Oktober"}
    assert kiosk.get_active_subtitle() == "Guild42 Meetup Oktober"
    # Every kiosk page without ?event= now shows it.
    assert '<div class="preview-sub" id="prev-sub">Guild42 Meetup Oktober</div>' in \
        client.get("/").get_data(as_text=True)


def test_steuerzeichen_raus_und_gekuerzt(client):
    import app as kiosk
    client.post("/api/v1/event", json={"event": "A\nB\tC\x00" + "x" * 80}, headers=AUTH)
    wert = kiosk.get_active_subtitle()
    assert wert.startswith("ABC") and len(wert) == kiosk.MAX_SUBTITLE
    assert all(c.isprintable() for c in wert)


@pytest.mark.parametrize("body", [{}, {"event": ""}, {"event": " \n\t"}, {"event": 42}, [], None])
def test_ungueltig_400_und_nichts_geaendert(client, body):
    import app as kiosk
    vorher = kiosk.get_active_subtitle()
    assert client.post("/api/v1/event", json=body, headers=AUTH).status_code == 400
    assert kiosk.get_active_subtitle() == vorher


def test_ohne_token_401_und_nichts_geaendert(client):
    import app as kiosk
    vorher = kiosk.get_active_subtitle()
    assert client.post("/api/v1/event", json={"event": "Fremd"}).status_code == 401
    assert client.post("/api/v1/event", json={"event": "Fremd"},
                       headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert kiosk.get_active_subtitle() == vorher


def test_get_event_bleibt_seitenlokal(client):
    import app as kiosk
    client.post("/api/v1/event", json={"event": "Global"}, headers=AUTH)
    html = client.get("/?event=Nur%20hier").get_data(as_text=True)
    assert '<div class="preview-sub" id="prev-sub">Nur hier</div>' in html
    assert kiosk.get_active_subtitle() == "Global"
