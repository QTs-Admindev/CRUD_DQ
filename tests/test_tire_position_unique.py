"""PR C: el índice UNIQUE de posición (uq_tire_mount_slot) es el backstop atómico contra
dos llantas montadas en la misma (unidad, eje, rueda). Cuando la BD lo rechaza en carrera,
el handler revierte el bind en la plataforma y responde 409 (no un 500).
"""
import json

from functions.bindings import bind_tire


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeSt:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))


class Store:
    def __init__(self, rows, dup_on_update=False):
        self.rows = rows
        self.dup_on_update = dup_on_update

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=200):
        return []  # el guard de posición en app no ve ocupada (simulamos la carrera)

    def update(self, db, table, rid, data):
        if self.dup_on_update:
            raise Exception("(1062, \"Duplicate entry '1-2-4' for key 'uq_tire_mount_slot'\")")
        self.rows[rid].update(data)
        return dict(self.rows[rid])


def _wire(monkeypatch, store, st):
    monkeypatch.setattr(bind_tire, "get_db", lambda: FakeDB())
    monkeypatch.setattr(bind_tire, "get_by_id", store.get_by_id)
    monkeypatch.setattr(bind_tire, "get_where", store.get_where)
    monkeypatch.setattr(bind_tire, "update", store.update)
    monkeypatch.setattr(bind_tire, "SmartTyreClient", lambda: st)
    monkeypatch.setattr(bind_tire, "audit", lambda *a, **k: None)


def _rows():
    return {
        1: {"id": 1, "daijin_id": 33369, "company_id": 100},
        10: {"id": 10, "is_mounted": 0, "unit_id": None, "sensor_id": None,
             "daijin_id": 414997, "company_id": 100, "is_deleted": 0, "folio": "F"},
    }


def _ev(uid, body):
    return {"pathParameters": {"id": str(uid)}, "body": json.dumps(body)}


def test_duplicate_position_returns_409_and_reverts_platform(monkeypatch):
    store, st = Store(_rows(), dup_on_update=True), FakeSt()
    _wire(monkeypatch, store, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 409                        # no 500
    # se hizo el bind y luego el UNbind de reversa en la plataforma
    paths = [p for p, _ in st.posts]
    assert "/smartyre/openapi/vehicle/tyre/bind" in paths
    assert "/smartyre/openapi/vehicle/tyre/unbind" in paths


def test_happy_path_still_mounts(monkeypatch):
    store, st = Store(_rows(), dup_on_update=False), FakeSt()
    _wire(monkeypatch, store, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["is_mounted"] == 1
