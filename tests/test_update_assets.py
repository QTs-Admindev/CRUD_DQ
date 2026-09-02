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

    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def get_by_id(self, db, table, rid):
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
