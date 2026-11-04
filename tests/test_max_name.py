"""Karte 1655: MAX_NAME-Vorgabe ist der Produktionswert 40, und der gueltige Wert steht beim Start im Log."""
import logging

import pytest


@pytest.fixture()
def ohne_max_name_env(monkeypatch):
    monkeypatch.delenv("MAX_NAME", raising=False)
    return None


def test_vorgabe_ist_40_installation_ohne_variable_schneidet_nicht_mehr_bei_15(kiosk, ohne_max_name_env):
    assert kiosk.max_name() == 40


def test_umgebung_darf_abweichung_setzen(kiosk, monkeypatch):
    monkeypatch.setenv("MAX_NAME", "25")
    assert kiosk.max_name() == 25


def test_umgebung_wird_nach_oben_geklemmt(kiosk, monkeypatch):
    monkeypatch.setenv("MAX_NAME", "500")
    assert kiosk.max_name() == 40


def test_umgebung_nach_unten_geklemmt(kiosk, monkeypatch):
    monkeypatch.setenv("MAX_NAME", "0")
    assert kiosk.max_name() == 1


def test_ungueltiger_wert_faellt_auf_vorgabe_zurueck(kiosk, monkeypatch):
    monkeypatch.setenv("MAX_NAME", "keine-zahl")
    assert kiosk.max_name() == 40


def test_start_schreibt_den_gueltigen_wert_ins_log(kiosk, ohne_max_name_env, caplog):
    # Das Modul loggt beim Import — genau das nachbilden, was ein Neustart unter gunicorn tut.
    import importlib
    with caplog.at_level(logging.INFO):
        importlib.reload(kiosk)
    assert "MAX_NAME=40" in caplog.text
