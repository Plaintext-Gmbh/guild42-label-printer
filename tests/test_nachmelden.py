"""Karte 1557: /nachmelden redirects to guild's late-registration form."""

import importlib

import pytest


@pytest.fixture()
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_SIM", "1")
    monkeypatch.chdir(tmp_path)
    import app as module
    importlib.reload(module)
    module.app.config["TESTING"] = True
    return module


def test_vorgabe_ist_guild(app_module, monkeypatch):
    monkeypatch.delenv("NACHMELDEN_URL", raising=False)
    antwort = app_module.app.test_client().get("/nachmelden?x=1")
    assert antwort.status_code == 302
    assert antwort.headers["Location"] == "https://app.guild42.ch/nachmelden"


def test_ziel_aus_variable(app_module, monkeypatch):
    monkeypatch.setenv("NACHMELDEN_URL", "https://int.example/nachmelden")
    antwort = app_module.app.test_client().get("/nachmelden")
    assert antwort.status_code == 302
    assert antwort.headers["Location"] == "https://int.example/nachmelden"


def test_startseite_leitet_bei_claim_auf_nachmelden(app_module, monkeypatch):
    monkeypatch.delenv("NACHMELDEN_URL", raising=False)
    monkeypatch.setattr(app_module, "_geclaimt", lambda: True)
    client = app_module.app.test_client()
    antwort = client.get("/")
    assert antwort.status_code == 302
    assert antwort.headers["Location"] == "https://app.guild42.ch/nachmelden"
    # ?event= ist der Desk-Weg aus guild: der bleibt auf der Druckerseite
    assert client.get("/?event=Testevent").status_code == 200


def test_startseite_ohne_claim_bleibt_druckerseite(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_geclaimt", lambda: False)
    assert app_module.app.test_client().get("/").status_code == 200
