"""Tests de los PUT de activos y de las dos guardas que se agregaron.

1. Una fila borrada (`is_deleted`) es una línea cerrada: los cuatro updates y los dos
   assign responden 404, igual que hacen los listados y el delete.
2. `status = 'active'` se CONFIRMA contra la plataforma; no se declara. Sin confirmación
   no se escribe nada, porque una fila activa sin `daijin_id` se sale de /resync y del
   cron de reconciliación.
"""
import json

import pytest

from shared import activation
from functions.sensors import assign as sensors_assign
from functions.sensors import update as sensors_update
from functions.tboxes import assign as tboxes_assign
from functions.tboxes import update as tboxes_update
from functions.tires import update as tires_update
from functions.vehicles import update as vehicles_update


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    """Almacén por id (no distingue tablas, igual que el de test_assign)."""

    def __init__(self, rows, companies=None):
        self.rows = rows
        self.updates = []
        # `companies` es tabla de referencia aparte: None = todas existen; una lista
        # acota cuáles, para probar el caso de la compañía que no existe.
        self.companies = companies

    def get_by_id(self, db, table, rid):
        if table == "companies":
            if self.companies is None or rid in self.companies:
                return {"id": rid, "name": "Compania"}
            return None
        r = self.rows.get(rid)
        return dict(r) if r else None

    def update(self, db, table, rid, data):
        self.updates.append((table, rid, dict(data)))
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def exists(self, db, table, filters):
        return False


def _wire(monkeypatch, mod, store, *, with_exists=False):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)
    # Por defecto no hay nada que estorbe (ni folio ocupado ni llantas que cascadear);
    # la prueba que necesite lo contrario lo sobrescribe después de llamar a _wire.
    monkeypatch.setattr(mod, "get_where", lambda *a, **k: [], raising=False)
    if with_exists:
        monkeypatch.setattr(mod, "exists", store.exists)


def _ev(rid, body):
    return {"pathParameters": {"id": str(rid)}, "body": json.dumps(body)}


# --------------------------------------------------------------- 1. is_deleted = 404

DELETED_CASES = [
    (sensors_update, {"id": 1, "is_deleted": 1, "sensorCode": "AA"}, {"status": "inactive"}),
    (tboxes_update, {"id": 1, "is_deleted": 1, "tboxCode": "BB"}, {"status": "inactive"}),
    (tires_update, {"id": 1, "is_deleted": 1, "folio": "F1"}, {"folio": "F2"}),
    (vehicles_update, {"id": 1, "is_deleted": 1, "unit_identifier": "U1"},
     {"unit_identifier": "U2"}),
]


@pytest.mark.parametrize("mod, row, body", DELETED_CASES)
def test_update_on_deleted_row_is_404(monkeypatch, mod, row, body):
    store = FakeStore({1: dict(row)})
    _wire(monkeypatch, mod, store)
    resp = mod.handler(_ev(1, body), None)
    assert resp["statusCode"] == 404
    assert store.updates == []          # no se escribió nada sobre la tumba


@pytest.mark.parametrize("mod", [sensors_assign, tboxes_assign])
def test_assign_on_deleted_row_is_404(monkeypatch, mod):
    store = FakeStore({1: {"id": 1, "is_deleted": 1, "company_id": None},
                       100: {"id": 100}})
    _wire(monkeypatch, mod, store, with_exists=True)
    resp = mod.handler(_ev(1, {"company_id": 100}), None)
    assert resp["statusCode"] == 404
    assert store.updates == []


@pytest.mark.parametrize("mod, row, body", DELETED_CASES)
def test_live_row_still_updates(monkeypatch, mod, row, body):
    # Contraprueba: la misma fila viva sí se edita (la guarda no bloquea de más).
    live = dict(row, is_deleted=0)
    store = FakeStore({1: live})
    _wire(monkeypatch, mod, store)
    resp = mod.handler(_ev(1, body), None)
    assert resp["statusCode"] == 200
    assert store.updates


# ------------------------------------------- 2. 'active' se confirma con la plataforma

DEVICE_CASES = [
    (sensors_update, "sensors", {"id": 1, "sensorCode": "AABBCCDDEEFF", "is_deleted": 0}),
    (tboxes_update, "tboxes", {"id": 1, "tboxCode": "112233445566", "is_deleted": 0}),
]


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_active_confirmed_on_platform_activates(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="inactive")})
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "confirm_on_platform", lambda rec, res, **k: "55")

    resp = mod.handler(_ev(1, {"status": "active"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["status"] == "active"
    # El id no cambió: no hay por qué reescribirlo.
    assert "daijin_id" not in store.updates[0][2]


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_active_without_platform_id_is_healed(monkeypatch, mod, resource, row):
    # La fila nunca guardó daijin_id pero la plataforma sí la tiene: se guarda el id
    # real y se activa (autocuración, mismo criterio que heal_on_resume).
    store = FakeStore({1: dict(row, daijin_id=None, status="registering")})
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "confirm_on_platform", lambda rec, res, **k: "900")

    resp = mod.handler(_ev(1, {"status": "active"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["daijin_id"] == "900"
    assert store.rows[1]["status"] == "active"


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_active_not_on_platform_is_409_and_writes_nothing(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id=None, status="registering")})
    _wire(monkeypatch, mod, store)

    def not_there(rec, res, **k):
        raise activation.NotOnPlatform("no está")
    monkeypatch.setattr(mod, "confirm_on_platform", not_there)

    resp = mod.handler(_ev(1, {"status": "active"}), None)

    assert resp["statusCode"] == 409
    assert store.updates == []
    assert store.rows[1]["status"] == "registering"   # sigue recuperable


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_active_when_platform_is_down_is_502_and_writes_nothing(
        monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id=None, status="registering")})
    _wire(monkeypatch, mod, store)

    def down(rec, res, **k):
        raise activation.PlatformUnavailable("timeout")
    monkeypatch.setattr(mod, "confirm_on_platform", down)

    resp = mod.handler(_ev(1, {"status": "active"}), None)

    assert resp["statusCode"] == 502
    assert store.updates == []
    assert store.rows[1]["status"] == "registering"


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_non_active_status_does_not_touch_the_platform(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active")})
    _wire(monkeypatch, mod, store)

    def boom(rec, res, **k):
        raise AssertionError("no se debe consultar la plataforma para 'inactive'")
    monkeypatch.setattr(mod, "confirm_on_platform", boom)

    resp = mod.handler(_ev(1, {"status": "inactive"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["status"] == "inactive"


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_invalid_status_still_422(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, status="active")})
    _wire(monkeypatch, mod, store)
    resp = mod.handler(_ev(1, {"status": "encendido"}), None)
    assert resp["statusCode"] == 422
    assert store.updates == []


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_already_active_and_synced_does_not_recheck(monkeypatch, mod, resource, row):
    """Editar la compañía de un activo ya sincronizado no consulta la plataforma.

    El modal manda siempre el status, así que sin esta condición una edición de
    compañía se caería con 502 cada vez que la plataforma esté lenta, sin motivo:
    la fila ya tiene id, que es el invariante que interesa proteger.
    """
    store = FakeStore({1: dict(row, daijin_id="55", status="active")})
    _wire(monkeypatch, mod, store)

    def boom(rec, res, **k):
        raise AssertionError("no debe consultar la plataforma: ya estaba activo con id")
    monkeypatch.setattr(mod, "confirm_on_platform", boom)

    resp = mod.handler(_ev(1, {"status": "active", "company_id": 300}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["company_id"] == 300


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_active_without_id_is_rechecked_even_if_status_says_active(
        monkeypatch, mod, resource, row):
    """La fila corrupta (activa sin id) sí se vuelve a confirmar: es la que se sana."""
    store = FakeStore({1: dict(row, daijin_id=None, status="active")})
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "confirm_on_platform", lambda rec, res, **k: "42")

    resp = mod.handler(_ev(1, {"status": "active"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["daijin_id"] == "42"


# --------------- el handler pregunta por el recurso y la llave correctos ---------------

@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_confirmation_asks_for_the_right_resource(monkeypatch, mod, resource, row):
    """Sin esto, pasar "units" en vez de "sensors" (mirar la placa en vez del código
    de hardware) dejaría todas las demás pruebas en verde."""
    store = FakeStore({1: dict(row, daijin_id=None, status="registering")})
    _wire(monkeypatch, mod, store)
    seen = {}

    def spy(rec, res, **k):
        seen["resource"] = res
        seen["rec_id"] = rec.get("id")
        seen["natural_key"] = rec.get("sensorCode") or rec.get("tboxCode")
        return "900"
    monkeypatch.setattr(mod, "confirm_on_platform", spy)

    mod.handler(_ev(1, {"status": "active"}), None)

    assert seen["resource"] == resource
    assert seen["rec_id"] == 1
    # La fila que se manda a confirmar es la del activo, con su llave de hardware.
    assert seen["natural_key"] == (row.get("sensorCode") or row.get("tboxCode"))


# ------------- la guarda también aplica en el sentido inverso (H6) -------------

@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_a_synced_row_cannot_be_sent_back_to_registering(monkeypatch, mod, resource, row):
    """Devolver a 'registering' una fila con id la deja en limbo: el barrido no la toca
    (tiene id) y el estado del importe masivo la cuenta como pendiente para siempre."""
    store = FakeStore({1: dict(row, daijin_id="55", status="active")})
    _wire(monkeypatch, mod, store)

    resp = mod.handler(_ev(1, {"status": "registering"}), None)

    assert resp["statusCode"] == 422
    assert store.updates == []
    assert store.rows[1]["status"] == "active"


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_a_row_without_id_can_still_be_marked_registering(monkeypatch, mod, resource, row):
    # Contraprueba: sin id, 'registering' es su estado legítimo y no se bloquea.
    store = FakeStore({1: dict(row, daijin_id=None, status="inactive")})
    _wire(monkeypatch, mod, store)

    resp = mod.handler(_ev(1, {"status": "registering"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["status"] == "registering"


# ------------------------------------------- 3. la compañía destino tiene que existir

COMPANY_CASES = [
    (sensors_update, {"id": 1, "status": "active", "daijin_id": "9", "sensorCode": "AA"}),
    (tboxes_update, {"id": 1, "status": "active", "daijin_id": "9", "tboxCode": "BB"}),
    (vehicles_update, {"id": 1, "status": "active", "daijin_id": "9",
                       "unit_identifier": "U1", "company_id": 7}),
]


@pytest.mark.parametrize("mod, row", COMPANY_CASES,
                         ids=["sensores", "qbox", "unidades"])
def test_no_se_mueve_a_una_compania_que_no_existe(monkeypatch, mod, row):
    """Un company_id cualquiera deja el activo colgado de una compañía fantasma: se
    sale de los listados (que filtran por compañía) sin estar borrado."""
    store = FakeStore({1: dict(row)}, companies=[7])
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "get_where", lambda *a, **k: [], raising=False)

    resp = mod.handler(_ev(1, {"company_id": 999}), None)

    assert resp["statusCode"] == 422
    assert store.updates == [], "se escribió antes de validar la compañía"


@pytest.mark.parametrize("mod, row", COMPANY_CASES,
                         ids=["sensores", "qbox", "unidades"])
def test_si_la_compania_existe_el_cambio_pasa(monkeypatch, mod, row):
    store = FakeStore({1: dict(row)}, companies=[7, 300])
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "get_where", lambda *a, **k: [], raising=False)

    resp = mod.handler(_ev(1, {"company_id": 300}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["company_id"] == 300


# ------------------------------------------------- 4. el update de llantas, en detalle

def _llanta(**extra):
    base = {"id": 1, "is_deleted": 0, "prefix": "QT", "folio": "202",
            "company_id": 7, "daijin_id": "33"}
    base.update(extra)
    return base


def test_la_llanta_se_edita_en_la_tabla_del_stage(monkeypatch):
    """El handler usaba la tabla literal `tires` en vez de `t("tires")`. Hoy no se
    nota porque el prefijo va vacío, pero en un stage con prefijo el editar sería el
    único endpoint escribiendo en otra tabla."""
    from shared import config

    monkeypatch.setattr(config, "TABLE_PREFIX", "qa_")
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 200
    assert store.updates[0][0] == "qa_tires"


def test_editar_la_llanta_deja_fecha(monkeypatch):
    """Sin `updated_at` no hay forma de saber cuándo se tocó, y el cool-off del cron
    (que se apoya en esa fecha) no protege a la fila recién editada."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)

    tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert store.updates[0][2].get("updated_at")


def test_un_folio_ya_tomado_es_409_y_no_un_500(monkeypatch):
    """(prefix, folio, company_id) es UNIQUE. El choque es error del usuario: antes
    salía como 500 con el SQL crudo dentro del mensaje."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "update",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("1062 Duplicate entry 'QT-303-7'")))

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 409
    assert "Duplicate" not in json.dumps(resp), "se devolvió SQL crudo al cliente"


@pytest.mark.parametrize("body", [{"folio": ""}, {"folio": "   "}, {"prefix": ""}])
def test_no_se_puede_dejar_la_llave_en_blanco(monkeypatch, body):
    """Mandar el campo vacío no es 'déjalo como está' (eso es no mandarlo): sería
    borrar media llave natural y dejar la llanta sin forma de nombrarse."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)

    resp = tires_update.handler(_ev(1, body), None)

    assert resp["statusCode"] == 422
    assert store.updates == []


def test_no_se_puede_escribir_la_marca_de_borrado_en_el_folio(monkeypatch):
    """`#del-` es infraestructura del índice UNIQUE. Si entrara como folio, la
    auditoría cortaría el valor por ahí y quedaría escrito a medias."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)

    resp = tires_update.handler(_ev(1, {"folio": "202#del-9"}), None)

    assert resp["statusCode"] == 422
    assert store.updates == []


def test_editar_la_llanta_queda_en_la_bitacora(monkeypatch):
    registrado = []
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "audit",
                        lambda *a, **k: registrado.append(k))

    tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert registrado, "el cambio de folio no dejó rastro"
    assert registrado[0]["asset_id"] == 1


def test_un_fallo_de_bitacora_no_tumba_una_edicion_ya_guardada(monkeypatch):
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "audit",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bitácora caída")))

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 200


def test_no_se_puede_editar_al_folio_de_otra_llanta_de_la_misma_compania(monkeypatch):
    """El folio es lo que el usuario lee para identificar la llanta: no se repite
    dentro de la compañía. El prefijo es visual y no cuenta, así que el índice
    (prefix, folio, company_id) por sí solo deja pasar el repetido."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(
        tires_update, "get_where",
        lambda db, table, where, params, limit: [{"id": 2, "prefix": "CEC",
                                                  "folio": "303", "company_id": 7}])

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 409
    assert store.updates == [], "se escribió el folio repetido"


def test_el_folio_libre_en_la_compania_si_pasa(monkeypatch):
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "get_where", lambda *a, **k: [])

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["folio"] == "303"


def test_cambiar_solo_el_prefijo_no_se_bloquea_a_si_mismo(monkeypatch):
    """La llanta no choca consigo misma: el folio no cambió."""
    consultas = []
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "get_where",
                        lambda *a, **k: consultas.append(a) or [])

    resp = tires_update.handler(_ev(1, {"prefix": "CEC"}), None)

    assert resp["statusCode"] == 200
    assert consultas == [], "consultó el folio sin que el folio cambiara"


def test_la_busqueda_del_folio_ignora_el_prefijo_y_a_la_llanta_misma(monkeypatch):
    """Dos detalles que no se ven desde el resultado y sin los cuales la regla es
    falsa: si la consulta filtrara por prefijo no vería a la gemela (que es justo
    la que hay que ver), y si no se excluyera a sí misma la llanta chocaría
    consigo al reeditarse."""
    visto = {}
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)

    def espia(db, table, where, params, limit):
        visto["where"] = where
        visto["params"] = params
        return []

    monkeypatch.setattr(tires_update, "get_where", espia)
    tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert "prefix" not in visto["where"], "la búsqueda filtra por prefijo"
    assert visto["params"][:2] == ["303", 7]
    assert 1 in visto["params"], "no se excluye a la llanta que se está editando"


def test_el_409_dice_CUAL_llanta_tiene_el_folio(monkeypatch):
    """Sin esto el usuario queda atorado: le dicen que está ocupado y no con qué.
    Y la llanta que estorba puede ser una que ni sabía que existía."""
    store = FakeStore({1: _llanta()})
    _wire(monkeypatch, tires_update, store)
    monkeypatch.setattr(tires_update, "get_where",
                        lambda *a, **k: [{"id": 4711, "prefix": "CEC",
                                          "folio": "303", "company_id": 7}])

    resp = tires_update.handler(_ev(1, {"folio": "303"}), None)

    assert resp["statusCode"] == 409
    cuerpo = json.dumps(resp)
    assert "4711" in cuerpo, "no dice el id de la llanta que lo tiene"
    assert "CEC" in cuerpo, "no dice el prefijo de la llanta que lo tiene"


# ------------------------------------------------------- lote (batch_code) al editar

@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_editar_pone_y_cambia_el_lote(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active", batch_code=None)})
    _wire(monkeypatch, mod, store)
    auditado = []
    monkeypatch.setattr(mod, "audit", lambda *a, **k: auditado.append(k))

    assert mod.handler(_ev(1, {"batch_code": "  LOTE-A "}), None)["statusCode"] == 200
    assert store.rows[1]["batch_code"] == "LOTE-A"
    assert mod.handler(_ev(1, {"batch_code": "LOTE-B"}), None)["statusCode"] == 200
    assert store.rows[1]["batch_code"] == "LOTE-B"

    # Cada cambio deja rastro con el valor de antes y el de después.
    assert [(a["payload"]["batch_code_antes"], a["changes"]["batch_code"]) for a in auditado] == [
        (None, "LOTE-A"), ("LOTE-A", "LOTE-B")]


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_editar_con_lote_vacio_lo_quita(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active", batch_code="LOTE-A")})
    _wire(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "audit", lambda *a, **k: None)

    assert mod.handler(_ev(1, {"batch_code": "   "}), None)["statusCode"] == 200
    assert store.rows[1]["batch_code"] is None


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_editar_sin_mandar_lote_no_lo_toca(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active", batch_code="LOTE-A")})
    _wire(monkeypatch, mod, store)
    auditado = []
    monkeypatch.setattr(mod, "audit", lambda *a, **k: auditado.append(k))

    assert mod.handler(_ev(1, {"status": "active"}), None)["statusCode"] == 200
    assert store.rows[1]["batch_code"] == "LOTE-A"
    assert all("batch_code" not in (a.get("changes") or {}) for a in auditado)


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_editar_con_el_mismo_lote_no_audita(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active", batch_code="LOTE-A")})
    _wire(monkeypatch, mod, store)
    auditado = []
    monkeypatch.setattr(mod, "audit", lambda *a, **k: auditado.append(k))

    assert mod.handler(_ev(1, {"batch_code": "LOTE-A"}), None)["statusCode"] == 200
    assert auditado == []


@pytest.mark.parametrize("mod, resource, row", DEVICE_CASES)
def test_editar_con_lote_invalido_es_422_y_no_escribe(monkeypatch, mod, resource, row):
    store = FakeStore({1: dict(row, daijin_id="55", status="active", batch_code=None)})
    _wire(monkeypatch, mod, store)

    assert mod.handler(_ev(1, {"batch_code": "X" * 65}), None)["statusCode"] == 422
    assert store.updates == []
