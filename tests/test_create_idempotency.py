"""PR F: en el camino "resume" (retomar una fila 'registering' existente) el create hace
GET-antes-de-POST (assume_new=False) para no insertar un duplicado upstream si un racer ya
lo está creando; en el camino "nuevo" mantiene assume_new=True. Y la conexión warm hace
rollback de cualquier transacción idle al reutilizarse.
"""
import json

from functions.tires import create as tire_mod
from functions.vehicles import create as veh_mod
import shared.db.connection as conn


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


def _ev(body):
    return {"body": json.dumps(body)}


# --------------------------------------------------------------- tires/create
def _wire_tire(monkeypatch, existing_row):
    calls = {}
    monkeypatch.setattr(tire_mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(tire_mod, "get_by_id",
                        lambda db, table, rid: {"id": rid} if table == "tires_catalog" else None)
    monkeypatch.setattr(tire_mod, "get_where",
                        lambda db, t, w, p=(), l=1: [existing_row] if existing_row else [])
    monkeypatch.setattr(tire_mod, "get_by_fields", lambda *a, **k: None)
    monkeypatch.setattr(tire_mod, "insert", lambda db, t, data: {"id": 7, **data})
    monkeypatch.setattr(tire_mod, "update", lambda db, t, rid, data: {"id": rid, **data})
    monkeypatch.setattr(tire_mod, "audit", lambda *a, **k: None)
    monkeypatch.setattr(tire_mod, "SmartTyreClient", lambda: object())

    def fake_roc(st, **kw):
        calls.update(kw)
        return "DID"
    monkeypatch.setattr(tire_mod, "resolve_or_create", fake_roc)
    return calls


def test_tire_resume_uses_get_before_post(monkeypatch):
    existing = {"id": 5, "prefix": "TEST", "folio": "F1", "company_id": 100,
                "daijin_id": None, "is_deleted": 0}
    calls = _wire_tire(monkeypatch, existing)
    resp = tire_mod.handler(_ev({"prefix": "TEST", "folio": "F1", "company_id": 100,
                                 "tires_catalog_id": 1}), None)
    assert resp["statusCode"] == 200
    assert calls["assume_new"] is False          # resume -> confirming GET-before-POST
    assert calls["list_filter"]["tyreCode"] == "5"


def test_tire_new_uses_assume_new(monkeypatch):
    calls = _wire_tire(monkeypatch, None)
    resp = tire_mod.handler(_ev({"prefix": "TEST", "folio": "F2", "company_id": 100,
                                 "tires_catalog_id": 1}), None)
    assert resp["statusCode"] == 200
    assert calls["assume_new"] is True           # nuevo -> insert directo


# --------------------------------------------------------------- vehicles/create
def test_vehicle_resume_uses_get_before_post(monkeypatch):
    existing = {"id": 9, "daijin_id": None, "unit_catalog_id": 1, "company_id": 100}
    calls = {}
    monkeypatch.setattr(veh_mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(veh_mod, "get_where", lambda db, t, w, p=(), l=1: [existing])
    monkeypatch.setattr(veh_mod, "get_by_id",
                        lambda db, table, rid: {"id": rid, "name": "truck", "type": "motive", "d_id": 3})
    monkeypatch.setattr(veh_mod, "update", lambda db, t, rid, data: {"id": rid, **data})
    monkeypatch.setattr(veh_mod, "audit", lambda *a, **k: None)
    monkeypatch.setattr(veh_mod, "SmartTyreClient", lambda: object())

    def fake_roc(st, **kw):
        calls.update(kw)
        return "VID"
    monkeypatch.setattr(veh_mod, "resolve_or_create", fake_roc)
    resp = veh_mod.handler(_ev({"unit_identifier": "TESTUNIT", "company_id": 100,
                                "unit_catalog_id": 1}), None)
    assert resp["statusCode"] in (200, 202)
    assert calls["assume_new"] is False


# --------------------------------------------------------------- connection warm reuse
def test_warm_connection_rolls_back_idle_tx(monkeypatch):
    class C:
        open = True

        def __init__(self):
            self.rb = 0

        def rollback(self):
            self.rb += 1

    c = C()
    conn._conn = c
    try:
        got = conn.get_db()
        assert got is c and c.rb == 1            # reutilizó y limpió la tx idle
    finally:
        conn._conn = None
