"""Alarmas del monitor de GPSHook (SNS) -> WhatsApp."""
import json

from functions.monitoring import sockets_alarm as sa


def _ev(**alarma):
    base = {"AlarmName": "gpshook-SinDatosGps-dev", "AlarmDescription": "Sockets: no llegan datos de GPS",
            "NewStateReason": "Threshold Crossed: 3 datapoints >= 900", "StateChangeTime": "2026-10-15T18:30:00.000+0000"}
    base.update(alarma)
    return {"Records": [{"Sns": {"Message": json.dumps(base)}}]}


def _capturar(monkeypatch):
    enviados = []
    monkeypatch.setattr(sa, "avisar", lambda **m: enviados.append(m) or 1)
    return enviados


def test_alarma_avisa(monkeypatch):
    enviados = _capturar(monkeypatch)
    assert sa.handler(_ev(NewStateValue="ALARM", OldStateValue="OK"), None) == {"sent": 1}
    m = enviados[0]
    assert m["tipo"].startswith("CRÍTICA")
    assert m["datos"] == "Sockets: no llegan datos de GPS"
    assert m["cuando"] == "15/10/2026 12:30 (CDMX)"


def test_recuperacion_avisa(monkeypatch):
    enviados = _capturar(monkeypatch)
    sa.handler(_ev(NewStateValue="OK", OldStateValue="ALARM"), None)
    assert enviados[0]["tipo"].startswith("RECUPERADO")


def test_ignora_insufficient_data(monkeypatch):
    enviados = _capturar(monkeypatch)
    sa.handler(_ev(NewStateValue="OK", OldStateValue="INSUFFICIENT_DATA"), None)
    sa.handler(_ev(NewStateValue="INSUFFICIENT_DATA", OldStateValue="OK"), None)
    assert enviados == []


def test_mensaje_roto_no_truena(monkeypatch):
    enviados = _capturar(monkeypatch)
    assert sa.handler({"Records": [{"Sns": {"Message": "no es json"}}]}, None) == {"sent": 0}
    assert enviados == []
