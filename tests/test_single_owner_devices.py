"""PR D: un Qbox pertenece a una sola unidad y un sensor a una sola llanta.
Guard previo (409 antes de tocar la plataforma) + backstop atómico (UNIQUE) que, si choca
en carrera, revierte el bind en la plataforma y devuelve 409.
"""
import json

from functions.vehicles import bind_tbox
from functions.bindings import bind_sensor


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeSt:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))


def _ev(pid, body):
    return {"pathParameters": {"id": str(pid)}, "body": json.dumps(body)}


# ----------------------------------------------------------------- Qbox (bind_tbox)
class TboxStore:
    def __init__(self, other_owner=False, dup=False):
        self.other_owner, self.dup = other_owner, dup
        self.rows = {
            1: {"id": 1, "daijin_id": "33369", "unit_catalog_id": 5, "company_id": 100, "tbox_id": None},
            20: {"id": 20, "daijin_id": "34351", "tboxCode": "10B41D30EA79", "company_id": 100},
            5: {"id": 5, "name": "truck", "type": "motive", "d_id": 7},
        }

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid); return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=1):
        return [{"id": 99}] if self.other_owner else []

    def update(self, db, table, rid, data):
        if self.dup:
            raise Exception("(1062, \"Duplicate entry '20' for key 'uq_unit_tbox_owner'\")")
        self.rows[rid].update(data); return dict(self.rows[rid])


def _wire_tbox(monkeypatch, store, st):
    monkeypatch.setattr(bind_tbox, "get_db", lambda: FakeDB())
    monkeypatch.setattr(bind_tbox, "get_by_id", store.get_by_id)
    monkeypatch.setattr(bind_tbox, "get_where", store.get_where)
    monkeypatch.setattr(bind_tbox, "update", store.update)
    monkeypatch.setattr(bind_tbox, "SmartTyreClient", lambda: st)
    monkeypatch.setattr(bind_tbox, "audit", lambda *a, **k: None)


def test_tbox_already_on_another_unit_409_no_post(monkeypatch):
    store, st = TboxStore(other_owner=True), FakeSt()
    _wire_tbox(monkeypatch, store, st)
    resp = bind_tbox.handler(_ev(1, {"tbox_id": 20}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []                                   # ni tocó la plataforma


def test_tbox_race_duplicate_409_and_reverts(monkeypatch):
    store, st = TboxStore(dup=True), FakeSt()
    _wire_tbox(monkeypatch, store, st)
    resp = bind_tbox.handler(_ev(1, {"tbox_id": 20}), None)
    assert resp["statusCode"] == 409
    # hizo el bind y luego lo revirtió (tboxCode vacío)
    assert any(b.get("tboxCode") == "" for _, b in st.posts)


# ----------------------------------------------------------------- sensor (bind_sensor)
class SensorStore:
    def __init__(self, other_owner=False):
        self.other_owner = other_owner
        self.rows = {
            10: {"id": 10, "is_mounted": 1, "unit_id": 1, "sensor_id": None,
                 "axle_index": 2, "wheel_index": 4, "company_id": 100, "daijin_id": 1},
            20: {"id": 20, "sensorCode": "A4C13873C3E6", "daijin_id": 275771, "company_id": 100},
            1: {"id": 1, "daijin_id": 33369, "company_id": 100},
        }

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid); return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=1):
        return [{"id": 88}] if self.other_owner else []

    def update(self, db, table, rid, data):
        self.rows[rid].update(data); return dict(self.rows[rid])


def test_sensor_already_on_another_tire_409_no_post(monkeypatch):
    store, st = SensorStore(other_owner=True), FakeSt()
    monkeypatch.setattr(bind_sensor, "get_db", lambda: FakeDB())
    monkeypatch.setattr(bind_sensor, "get_by_id", store.get_by_id)
    monkeypatch.setattr(bind_sensor, "get_where", store.get_where)
    monkeypatch.setattr(bind_sensor, "update", store.update)
    monkeypatch.setattr(bind_sensor, "SmartTyreClient", lambda: st)
    monkeypatch.setattr(bind_sensor, "audit", lambda *a, **k: None)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []
