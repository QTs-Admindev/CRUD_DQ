"""
test_aviso_critico.py
~~~~~~~~~~~~~~~~~~~~~
Avisar por WhatsApp cuando algo crítico queda a medias.

Lo que se prueba no es que el mensaje salga bonito, sino las tres propiedades sin
las cuales un sistema de avisos hace más daño que bien:

1. Está APAGADO por defecto. Un despliegue no puede empezar a mandar mensajes
   porque sí, y menos a un teléfono.
2. NO rompe a quien lo llama. Un fallo al avisar no puede convertir una operación
   buena en un error ni cambiar el código de respuesta de una mala.
3. NO grita. Se avisa de lo que necesita a una persona, y una sola vez por
   corrida. Avisar de lo que se arregla solo es la forma más común de que la
   gente deje de leer los avisos.
"""
import json
from unittest.mock import patch

import pytest

from shared import whatsapp
from shared.alerta_critica import describir_activo, notificar, notificar_resumen_barrido


@pytest.fixture
def enviados(monkeypatch):
    """Intercepta el envío: devuelve la lista de (tipo, datos, detalle, cuando)."""
    salidos = []
    monkeypatch.setattr(whatsapp, "avisar",
                        lambda tipo, datos, detalle, cuando:
                        salidos.append((tipo, datos, detalle, cuando)) or 1)
    import shared.alerta_critica as ac
    monkeypatch.setattr(ac, "avisar",
                        lambda tipo, datos, detalle, cuando:
                        salidos.append((tipo, datos, detalle, cuando)) or 1)
    return salidos


# ─── 1. Apagado por defecto ──────────────────────────────────────────────────

def test_sin_la_bandera_no_sale_ni_un_mensaje(monkeypatch):
    """Lo primero que tiene que ser cierto: desplegar esto no manda nada."""
    monkeypatch.setitem(whatsapp.CONFIG, "enabled", False)
    # Todo lo demás en condiciones de mandar: así lo único que puede detenerlo es
    # la bandera. Sin esto, la guarda de configuración tapaba a esta.
    monkeypatch.setitem(whatsapp.CONFIG, "token", "t")
    monkeypatch.setitem(whatsapp.CONFIG, "phone_id", "1")
    monkeypatch.setitem(whatsapp.CONFIG, "recipients", ["521"])
    llamadas = []
    monkeypatch.setattr(whatsapp, "_post", lambda payload: llamadas.append(payload) or True)

    assert whatsapp.avisar("t", "d", "x", "hoy") == 0
    assert llamadas == [], "se llamó a Meta con la notificación apagada"


def test_encendido_pero_sin_configurar_tampoco_manda(monkeypatch):
    """Media configuración es peor que ninguna: fallaría en cada corrida."""
    monkeypatch.setitem(whatsapp.CONFIG, "enabled", True)
    monkeypatch.setitem(whatsapp.CONFIG, "token", None)
    monkeypatch.setitem(whatsapp.CONFIG, "phone_id", "1")
    monkeypatch.setitem(whatsapp.CONFIG, "recipients", ["521"])
    llamadas = []
    monkeypatch.setattr(whatsapp, "_post", lambda payload: llamadas.append(payload) or True)

    assert whatsapp.avisar("t", "d", "x", "hoy") == 0
    assert llamadas == []


def test_el_numero_no_esta_escrito_en_el_codigo():
    """El destino es configuración de operación: cambia sin que cambie el software,
    y un repo se comparte."""
    import inspect
    fuente = inspect.getsource(whatsapp) + inspect.getsource(
        __import__("shared.alerta_critica", fromlist=["x"]))
    assert "2860" not in fuente and "446265" not in fuente


# ─── 2. No rompe a quien lo llama ────────────────────────────────────────────

def test_si_meta_truena_el_aviso_no_propaga(monkeypatch):
    monkeypatch.setitem(whatsapp.CONFIG, "enabled", True)
    monkeypatch.setitem(whatsapp.CONFIG, "token", "t")
    monkeypatch.setitem(whatsapp.CONFIG, "phone_id", "1")
    monkeypatch.setitem(whatsapp.CONFIG, "recipients", ["521"])
    monkeypatch.setattr(whatsapp.httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("sin red")))

    assert whatsapp.avisar("t", "d", "x", "hoy") == 0     # no lanza


def test_un_destinatario_caido_no_cancela_al_resto(monkeypatch):
    monkeypatch.setitem(whatsapp.CONFIG, "enabled", True)
    monkeypatch.setitem(whatsapp.CONFIG, "token", "t")
    monkeypatch.setitem(whatsapp.CONFIG, "phone_id", "1")
    monkeypatch.setitem(whatsapp.CONFIG, "recipients", ["A", "B", "C"])
    vistos = []

    def post(payload):
        vistos.append(payload["to"])
        return payload["to"] != "B"

    monkeypatch.setattr(whatsapp, "_post", post)
    assert whatsapp.avisar("t", "d", "x", "hoy") == 2
    assert vistos == ["A", "B", "C"], "se cortó el envío en el que falló"


def test_notificar_se_traga_cualquier_error(monkeypatch):
    import shared.alerta_critica as ac
    monkeypatch.setattr(ac, "avisar",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert notificar(motivo="borrado_rechazado", tipo_activo="llanta", rec={"id": 1}) == 0


def test_el_borrado_rechazado_sigue_contestando_409_aunque_el_aviso_falle(monkeypatch):
    """La respuesta al usuario no puede depender de que WhatsApp esté vivo."""
    from functions.tires import delete as tdel
    from tests.test_soft_delete import FakeRemote, FakeStore, _wire
    from shared.smarttyre import basic_api

    store = FakeStore({7: {"id": 7, "is_deleted": 0, "folio": "202", "daijin_id": "33"}})
    _wire(monkeypatch, tdel, store, FakeRemote((basic_api.GUARD, "ya tiene sensor")))
    monkeypatch.setattr(tdel, "notificar",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("whatsapp caído")))

    resp = tdel.handler({"pathParameters": {"id": "7"}}, None)

    assert resp["statusCode"] == 409
    assert store.rows[7]["is_deleted"] == 0, "se borró local con la plataforma viva"


# ─── 3. No grita, y dice lo necesario ────────────────────────────────────────

def test_una_corrida_limpia_no_avisa_nada(enviados):
    notificar_resumen_barrido({"resolved": 5, "deleted": 2, "verified": 80,
                               "guard_blocked": 0, "phantom_cleared": 0, "errors": 0})
    assert enviados == [], "avisó de una corrida en la que no hay nada que hacer"


@pytest.mark.parametrize("campo", ["guard_blocked", "phantom_cleared", "errors"])
def test_lo_que_necesita_a_una_persona_si_avisa(enviados, campo):
    notificar_resumen_barrido({campo: 3})
    assert len(enviados) == 1


def test_ochenta_fallas_son_UN_mensaje_no_ochenta(enviados):
    """Si 80 filas fallan, 80 mensajes no informan más que uno y sí garantizan que
    nadie los lea."""
    notificar_resumen_barrido({"guard_blocked": 40, "phantom_cleared": 40, "errors": 12})
    assert len(enviados) == 1
    assert "40" in enviados[0][1] and "12" in enviados[0][1]


def test_el_aviso_trae_con_que_encontrar_la_llanta(enviados):
    notificar(motivo="borrado_rechazado", tipo_activo="llanta",
              rec={"id": 26887, "prefix": "L5", "folio": "82512", "company_id": 8,
                   "unit_id": 45, "mount_position": 10},
              lado="plataforma", detalle="la llanta aún tiene sensor",
              event={"headers": {"X-Actor": "alguien@quinta.tech"}})

    _tipo, datos, detalle, _cuando = enviados[0]
    for dato in ("26887", "L5", "82512", "8", "45", "10"):
        assert dato in datos, f"falta {dato} en el mensaje"
    assert "plataforma" in detalle
    assert "alguien@quinta.tech" in detalle, "no dice quién lo hizo"
    assert "sensor" in detalle, "no dice qué falló"


def test_del_cron_el_responsable_es_el_cron(enviados):
    notificar(motivo="barrido_caido", tipo_activo="barrido", detalle="auth 500",
              actor="cron")
    assert "cron" in enviados[0][2]


def test_un_detalle_larguisimo_no_revienta_la_plantilla(enviados):
    notificar(motivo="borrado_rechazado", tipo_activo="llanta", rec={"id": 1},
              detalle="x" * 5000)
    assert len(enviados[0][2]) < 400


def test_el_sensor_se_nombra_por_su_codigo():
    d = describir_activo({"id": 9, "sensorCode": "A4C138D844B2", "company_id": 3}, "sensor")
    assert "A4C138D844B2" in d and "9" in d


def test_el_mensaje_no_lleva_secretos(monkeypatch):
    """El payload que sale a Meta no debe cargar nada que no sirva para actuar."""
    monkeypatch.setitem(whatsapp.CONFIG, "enabled", True)
    monkeypatch.setitem(whatsapp.CONFIG, "token", "TOKEN-SECRETO")
    monkeypatch.setitem(whatsapp.CONFIG, "phone_id", "1")
    monkeypatch.setitem(whatsapp.CONFIG, "recipients", ["521"])
    capturado = {}
    monkeypatch.setattr(whatsapp, "_post", lambda p: capturado.update(p) or True)

    whatsapp.avisar("tipo", "datos", "detalle", "hoy")

    assert "TOKEN-SECRETO" not in json.dumps(capturado)


def test_el_borrado_que_la_plataforma_rechaza_SI_avisa(monkeypatch):
    """Es el caso que más importa: aquí se acabaron los reintentos. El cron tampoco
    lo va a cerrar, y hasta que alguien desvincule a mano la llanta no se puede
    borrar ni se libera su folio."""
    from functions.tires import delete as tdel
    from tests.test_soft_delete import FakeRemote, FakeStore, _wire
    from shared.smarttyre import basic_api

    avisos = []
    store = FakeStore({7: {"id": 7, "is_deleted": 0, "prefix": "L5", "folio": "82512",
                           "company_id": 8, "daijin_id": "33"}})
    _wire(monkeypatch, tdel, store, FakeRemote((basic_api.GUARD, "la llanta aún tiene sensor")))
    monkeypatch.setattr(tdel, "notificar", lambda **k: avisos.append(k) or 1)

    resp = tdel.handler({"pathParameters": {"id": "7"},
                         "headers": {"X-Actor": "alguien@quinta.tech"}}, None)

    assert resp["statusCode"] == 409
    assert len(avisos) == 1, "nadie se entera de un borrado que quedó trabado"
    assert avisos[0]["rec"]["folio"] == "82512"
    assert "sensor" in avisos[0]["detalle"]
