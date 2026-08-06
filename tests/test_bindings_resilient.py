"""Regresión: si la plataforma NO confirma el binding (read-back), el handler responde
202 `pending` y audita `pending` — NUNCA un falso 200. La intención local sí se graba para
que el barrido de reconciliación pueda completarla.

El fixture autouse (conftest) hace que verify.* confirme por defecto; aquí forzamos que
NO confirme para probar el camino divergente.
"""
import json

from shared.smarttyre import verify
from functions.vehicles import bind_tbox, unbind_tbox
from functions.bindings import bind_sensor, unbind_sensor, bind_tire, unbind_tire


def _ev(path_id, body):
    return {"pathParameters": {"id": str(path_id)}, "body": json.dumps(body)}


def _body(resp):
    return json.loads(resp["body"])


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=200):
        return []  # sin colisiones de posición en estos tests


class FakeSt:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))


def _wire(monkeypatch, module, store, db, st, unconfirmed):
    monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setattr(module, "get_by_id", store.get_by_id)
    monkeypatch.setattr(module, "update", store.update)
    monkeypatch.setattr(module, "SmartTyreClient", lambda: st)
    if hasattr(module, "get_where"):
        monkeypatch.setattr(module, "get_where", store.get_where)
    audits = []
    if hasattr(module, "audit"):
        monkeypatch.setattr(module, "audit",
                            lambda *a, **k: audits.append(k) or None)
    # La plataforma NO confirma el estado buscado.
    for name in unconfirmed:
        monkeypatch.setattr(verify, name, lambda *a, **k: False)
    return audits


def _seed():
    return {
        1: {"id": 1, "daijin_id": "33369", "unit_catalog_id": 5, "company_id": 100,
            "tbox_id": None, "status": "active"},
        5: {"id": 5, "name": "truck", "type": "motive", "d_id": 7},
        10: {"id": 10, "is_mounted": 0, "unit_id": None, "sensor_id": None,
             "axle_index": None, "wheel_index": None, "company_id": 100,
             "daijin_id": 414997, "is_deleted": 0, "folio": "F1"},
        20: {"id": 20, "sensorCode": "A4C13873C3E6", "daijin_id": 275771, "company_id": 100},
    }


def test_bind_tbox_not_confirmed_is_pending(monkeypatch):
    store = FakeStore(_seed())
    store.rows[20] = {"id": 20, "daijin_id": "34351", "tboxCode": "10B41D30EA79", "company_id": 100}
    db, st = FakeDB(), FakeSt()
    audits = _wire(monkeypatch, bind_tbox, store, db, st, ["tbox_bound"])
    resp = bind_tbox.handler(_ev(1, {"tbox_id": 20}), None)
    assert resp["statusCode"] == 202                       # NO 200
    assert _body(resp)["data"]["tbox_id"] == 20            # intención local grabada
    assert any(a.get("result") == "pending" for a in audits)


def test_unbind_tbox_not_confirmed_is_pending(monkeypatch):
    rows = _seed()
    rows[1]["tbox_id"] = 20
    rows[20] = {"id": 20, "daijin_id": "34351", "tboxCode": "10B41D30EA79", "company_id": 100}
    store, db, st = FakeStore(rows), FakeDB(), FakeSt()
    audits = _wire(monkeypatch, unbind_tbox, store, db, st, ["tbox_unbound"])
    resp = unbind_tbox.handler(_ev(1, {}), None)
    assert resp["statusCode"] == 202
    assert store.rows[1]["tbox_id"] is None
    assert any(a.get("result") == "pending" for a in audits)


def test_bind_sensor_not_confirmed_is_pending(monkeypatch):
    rows = _seed()
    rows[10].update({"is_mounted": 1, "unit_id": 1, "axle_index": 2, "wheel_index": 4})
    store, db, st = FakeStore(rows), FakeDB(), FakeSt()
    audits = _wire(monkeypatch, bind_sensor, store, db, st, ["sensor_on_tyre"])
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 202
    assert _body(resp)["data"]["synced_to_platform"] is False
    assert store.rows[10]["sensor_id"] == 20
    assert any(a.get("result") == "pending" for a in audits)


def test_unbind_sensor_not_confirmed_is_pending(monkeypatch):
    rows = _seed()
    rows[10].update({"is_mounted": 1, "unit_id": 1, "sensor_id": 20})
    store, db, st = FakeStore(rows), FakeDB(), FakeSt()
    audits = _wire(monkeypatch, unbind_sensor, store, db, st, ["sensor_off_tyre"])
    resp = unbind_sensor.handler(_ev(10, {}), None)
    assert resp["statusCode"] == 202
    assert store.rows[10]["sensor_id"] is None
    assert any(a.get("result") == "pending" for a in audits)


def test_bind_tire_not_confirmed_is_pending(monkeypatch):
    rows = _seed()
    store, db, st = FakeStore(rows), FakeDB(), FakeSt()
    audits = _wire(monkeypatch, bind_tire, store, db, st, ["tyre_on_vehicle"])
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 202
    assert store.rows[10]["is_mounted"] == 1               # intención local grabada
    assert any(a.get("result") == "pending" for a in audits)


def test_unbind_tire_not_confirmed_is_pending(monkeypatch):
    rows = _seed()
    rows[10].update({"is_mounted": 1, "unit_id": 1})
    store, db, st = FakeStore(rows), FakeDB(), FakeSt()
    audits = _wire(monkeypatch, unbind_tire, store, db, st, ["tyre_off_vehicle"])
    resp = unbind_tire.handler(_ev(1, {"tire_id": 10}), None)
    assert resp["statusCode"] == 202
    assert store.rows[10]["is_mounted"] == 0
    assert any(a.get("result") == "pending" for a in audits)
