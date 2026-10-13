"""Kiosk page with ?event=<title>: where guild42 sends the desk after onboarding.

Contract: the event shows on this page and is what this page prints, it is escaped, capped, and
it never touches the global event (runtime.txt) - a link must not change the kiosk for everyone.
"""

import importlib

import pytest


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_SIM", "1")
    monkeypatch.chdir(tmp_path)
    import app as app_module
    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "RUNTIME_FILE", str(tmp_path / "runtime.txt"))
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_event_parameter_wird_angezeigt_und_gedruckt(client):
    html = client.get("/?event=Guild42%20Meetup%20Oktober").get_data(as_text=True)
    assert '<div class="preview-sub" id="prev-sub">Guild42 Meetup Oktober</div>' in html
    assert 'const FIXED_EVENT = "Guild42 Meetup Oktober";' in html
    # The admin panel would switch the GLOBAL event and contradict the fixed one - hidden.
    assert 'id="admin-panel"' not in html


def test_event_parameter_aendert_den_globalen_anlass_nicht(client):
    import app as kiosk
    client.get("/?event=Fremder%20Anlass")
    assert kiosk.get_active_subtitle() == kiosk.get_default_subtitle()
    html = client.get("/").get_data(as_text=True)
    assert "Fremder Anlass" not in html
    assert 'id="admin-panel"' in html
    assert 'const FIXED_EVENT = "";' in html


def test_event_parameter_wird_escaped_und_gekuerzt(client):
    import app as kiosk
    html = client.get("/?event=</script><b>x" + "y" * 80).get_data(as_text=True)
    assert "</script><b>" not in html
    assert "y" * (kiosk.MAX_SUBTITLE - len("</script><b>x")) in html
    assert "y" * (kiosk.MAX_SUBTITLE - len("</script><b>x") + 1) not in html
