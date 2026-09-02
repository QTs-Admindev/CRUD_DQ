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
