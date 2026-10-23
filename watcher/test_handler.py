"""Vigía de la plataforma: reglas de aviso, mensaje y modo prueba.

Se protege: un parpadeo no despierta a nadie (2 fallas seguidas), no se repite
el aviso en cada corrida (cada 6 h), al volver se reinicia, el mensaje lleva las
8 variables de la plantilla y apagado no manda nada.
"""
import handler as h


def _cfg(**kw):
    base = {"enabled": True, "template": "platform_incident_detected", "lang": "en",
            "token": "t", "phone_id": "p", "api_version": "v23.0",
            "recipients": ["5210000000000"], "table": "x"}
    return {**base, **kw}


def _estados(**caidos):
    e = {nombre: None for nombre, _ in h.SERVICIOS}
    e.update({k.replace("_", " "): v for k, v in caidos.items()})
    return e


def test_reporte_lleva_los_seis_servicios_en_8_variables():
    v = h.variables_reporte(_estados(Admin="HTTP 502"), 1_761_220_800_000)
    assert len(v) == 8 and all(v)
    assert v[0].startswith("Reporte diario") and "(CDMX)" in v[0]
    assert v[1] == "1 caído(s): Admin"
    assert "Admin: HTTP 502" in v[2:]
    assert h.variables_reporte(_estados(), 0)[1] == "Todo en línea"


def test_reporte_apagado_no_manda(monkeypatch):
    monkeypatch.setattr(h, "revisar", lambda url: None)
    monkeypatch.setattr(h, "config", lambda: _cfg(enabled=False))
    assert h.reporte({}, None)["enviados"] == 0


def test_parpadeo_no_avisa_y_dos_fallas_si(monkeypatch):
    enviados = []
    monkeypatch.setattr(h, "enviar", lambda cfg, v: enviados.append(v) or 1)
    est = _estados(Admin="HTTP 502")
    previo, _ = h.ciclo(est, {}, 1_000, _cfg())
    assert enviados == []
    _, avisos = h.ciclo(est, previo, 301_000, _cfg())
    assert avisos == 1
    v = enviados[0]
    assert len(v) == 8 and all(v)
    assert v[0] == "Admin" and v[1] == "HTTP 502"
    assert "admin.quinta.tech" in v[2] and "2 revisiones" in v[2]
    assert "Dashboard (dev): OK" in v[3:]


def test_no_repite_hasta_6_horas_y_al_volver_reinicia(monkeypatch):
    enviados = []
    monkeypatch.setattr(h, "enviar", lambda cfg, v: enviados.append(v) or 1)
    est, previo, t = _estados(Admin="HTTP 500"), {}, 0
    for _ in range(6):
        previo, _ = h.ciclo(est, previo, t, _cfg()); t += 300_000
    assert len(enviados) == 1
    previo, _ = h.ciclo(est, previo, t + h.REPETIR_CADA_MS, _cfg())
    assert len(enviados) == 2
    previo, _ = h.ciclo(_estados(), previo, t + h.REPETIR_CADA_MS + 1, _cfg())
    assert previo["Admin"]["fallas"] == 0 and "ultimo_aviso" not in previo["Admin"]


def test_otro_caido_aparece_en_el_estado(monkeypatch):
    enviados = []
    monkeypatch.setattr(h, "enviar", lambda cfg, v: enviados.append(v) or 1)
    est = _estados(Admin="HTTP 500", **{"Dashboard (dev)": "HTTP 500"})
    previo, _ = h.ciclo(est, {}, 0, _cfg())
    h.ciclo(est, previo, 300_000, _cfg())
    admin = [v for v in enviados if v[0] == "Admin"][0]
    assert "Dashboard (dev): caído" in admin


def test_apagado_o_sin_credenciales_no_manda(monkeypatch):
    llamadas = []
    monkeypatch.setattr(h.urllib.request, "urlopen", lambda *a, **k: llamadas.append(a))
    assert h.enviar(_cfg(enabled=False), ["x"] * 8) == 0
    assert h.enviar(_cfg(token=""), ["x"] * 8) == 0
    assert llamadas == []


def test_payload_de_la_plantilla(monkeypatch):
    capturado = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        capturado["url"], capturado["body"] = req.full_url, req.data
        return R()

    monkeypatch.setattr(h.urllib.request, "urlopen", urlopen)
    assert h.enviar(_cfg(), [str(i) for i in range(8)]) == 1
    import json
    cuerpo = json.loads(capturado["body"])
    assert capturado["url"].endswith("/p/messages")
    assert cuerpo["template"]["name"] == "platform_incident_detected"
    assert cuerpo["template"]["language"] == {"code": "en"}
    assert len(cuerpo["template"]["components"][0]["parameters"]) == 8


def test_prueba_no_toca_el_estado(monkeypatch):
    monkeypatch.setattr(h, "revisar", lambda url: None)
    monkeypatch.setattr(h, "enviar", lambda cfg, v: 1)

    class SinEstado:
        def __init__(self, *_): raise AssertionError("la prueba no debe abrir el estado")

    r = h.check({"prueba": True}, None, estado_cls=SinEstado)
    assert r["prueba"] and r["enviados"] == 1
