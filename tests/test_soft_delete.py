import json

from functions.sensors import delete as sdel
from functions.tboxes import delete as bdel
from functions.tires import delete as tdel
from functions.vehicles import delete as vdel
from shared.smarttyre import basic_api

DONE, GUARD, TRANSIENT = basic_api.DONE, basic_api.GUARD, basic_api.TRANSIENT


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self, rows, bound=False):
        self.rows = rows
        self.bound = bound          # what exists() returns (asset bound or not)
        self.soft_deleted = []      # ids passed to soft_delete (keeps daijin_id)
        self.updated = []           # (id, data) passed to update

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def soft_delete(self, db, table, rid):
        self.soft_deleted.append(rid)
        self.rows[rid]["is_deleted"] = 1
        return dict(self.rows[rid])

    def update(self, db, table, rid, data):
        self.updated.append((rid, data))
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def exists(self, db, table, filters):
        return self.bound


class FakeRemote:
    """Stand-in for attempt_delete: records calls, returns a fixed outcome."""
    def __init__(self, outcome=(DONE, None)):
        self.outcome = outcome
        self.calls = []

    def __call__(self, resource, daijin_id, *a, **k):
        self.calls.append((resource, daijin_id))
        return self.outcome


def _wire(monkeypatch, mod, store, remote):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "soft_delete", store.soft_delete)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "exists", store.exists, raising=False)
    monkeypatch.setattr(mod, "attempt_delete", remote)


def _ev(rid):
    return {"pathParameters": {"id": str(rid)}}


# --- happy path: Dajin confirma -> soft-delete + limpia daijin_id (200) ---
def test_vehicle_delete_dajin_ok(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["is_deleted"] == 1
    assert store.rows[1]["daijin_id"] is None        # cerrado
    assert remote.calls == [("vehicle", "33")]


# --- sin daijin_id: nunca sincronizó -> solo local, no llama a Dajin ---
def test_vehicle_delete_without_daijin_skips_remote(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": None}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["is_deleted"] == 1
    assert remote.calls == []                          # no se tocó Dajin


# --- guard de Dajin (ej. 531): 409 y NO se toca local ---
def test_vehicle_delete_dajin_guard_aborts(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})
    remote = FakeRemote((GUARD, "轮胎已绑定传感器"))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 409
    assert store.rows[1]["is_deleted"] == 0            # intacto
    assert store.soft_deleted == [] and store.updated == []


# --- transitorio: Dajin no responde -> soft-delete local + 202, conserva daijin_id ---
def test_vehicle_delete_transient_pending(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})
    remote = FakeRemote((TRANSIENT, "timeout"))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 202
    assert store.rows[1]["is_deleted"] == 1
    assert store.rows[1]["daijin_id"] == "33"          # se conserva -> reconciliación
    assert store.soft_deleted == [1] and store.updated == []


# --- guard local (vinculado): 409 y NO se llama a Dajin ---
def test_vehicle_delete_blocked_when_bound(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}}, bound=True)
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 409
    assert remote.calls == []                          # ni siquiera se intentó en Dajin


def test_vehicle_not_found_404(monkeypatch):
    store = FakeStore({})
    _wire(monkeypatch, vdel, store, FakeRemote())
    assert vdel.handler(_ev(99), None)["statusCode"] == 404


def test_already_deleted_is_idempotent(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 1}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert remote.calls == []


def test_tire_delete_dajin_ok(monkeypatch):
    store = FakeStore({5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": None,
                           "daijin_id": "77"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    assert store.rows[5]["is_deleted"] == 1
    assert remote.calls == [("tyre", "77")]


def test_tire_delete_blocked_when_mounted_or_has_sensor(monkeypatch):
    store = FakeStore({5: {"id": 5, "is_deleted": 0, "unit_id": 1, "sensor_id": None,
                           "daijin_id": "77"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 409
    assert remote.calls == []


def test_tire_delete_blocked_when_has_sensor(monkeypatch):
    store = FakeStore({5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": 9,
                           "daijin_id": "77"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, tdel, store, remote)
    assert tdel.handler(_ev(5), None)["statusCode"] == 409
    assert remote.calls == []


# ---------- sensor y tbox: mismo contrato Dajin-first ----------

def test_sensor_delete_dajin_ok(monkeypatch):
    store = FakeStore({3: {"id": 3, "is_deleted": 0, "daijin_id": "280080"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, sdel, store, remote)
    resp = sdel.handler(_ev(3), None)
    assert resp["statusCode"] == 200
    assert store.rows[3]["is_deleted"] == 1
    assert store.rows[3]["daijin_id"] is None
    assert remote.calls == [("sensor", "280080")]


def test_sensor_delete_blocked_when_bound_to_tire(monkeypatch):
    store = FakeStore({3: {"id": 3, "is_deleted": 0, "daijin_id": "280080"}}, bound=True)
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, sdel, store, remote)
    assert sdel.handler(_ev(3), None)["statusCode"] == 409
    assert remote.calls == []


def test_sensor_delete_transient_pending(monkeypatch):
    store = FakeStore({3: {"id": 3, "is_deleted": 0, "daijin_id": "280080"}})
    remote = FakeRemote((TRANSIENT, "timeout"))
    _wire(monkeypatch, sdel, store, remote)
    resp = sdel.handler(_ev(3), None)
    assert resp["statusCode"] == 202
    assert store.rows[3]["daijin_id"] == "280080"      # conservado para reconciliación


def test_tbox_delete_dajin_ok(monkeypatch):
    store = FakeStore({7: {"id": 7, "is_deleted": 0, "daijin_id": "34616"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, bdel, store, remote)
    resp = bdel.handler(_ev(7), None)
    assert resp["statusCode"] == 200
    assert remote.calls == [("tbox", "34616")]


def test_tbox_delete_blocked_when_assigned(monkeypatch):
    store = FakeStore({7: {"id": 7, "is_deleted": 0, "daijin_id": "34616"}}, bound=True)
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, bdel, store, remote)
    assert bdel.handler(_ev(7), None)["statusCode"] == 409
    assert remote.calls == []


def test_tbox_delete_guard_aborts(monkeypatch):
    store = FakeStore({7: {"id": 7, "is_deleted": 0, "daijin_id": "34616"}})
    remote = FakeRemote((GUARD, "vinculado a vehículo"))
    _wire(monkeypatch, bdel, store, remote)
    resp = bdel.handler(_ev(7), None)
    assert resp["statusCode"] == 409
    assert store.rows[7]["is_deleted"] == 0


# ---------- inputs inválidos ----------

def test_invalid_id_returns_400(monkeypatch):
    store = FakeStore({})
    for mod in (vdel, tdel, sdel, bdel):
        _wire(monkeypatch, mod, store, FakeRemote())
        assert mod.handler({"pathParameters": {"id": "abc"}}, None)["statusCode"] == 400


def test_missing_path_parameters_returns_400(monkeypatch):
    store = FakeStore({})
    for mod in (vdel, tdel, sdel, bdel):
        _wire(monkeypatch, mod, store, FakeRemote())
        assert mod.handler({}, None)["statusCode"] == 400
        assert mod.handler({"pathParameters": None}, None)["statusCode"] == 400


# ---------- idempotencia con borrado remoto pendiente ----------

def test_already_deleted_with_pending_daijin_does_not_retry_inline(monkeypatch):
    # Quedó is_deleted=1 con daijin_id (pendiente): el DELETE repetido responde 200
    # sin volver a llamar a Dajin — la reconciliación es la dueña del reintento.
    store = FakeStore({1: {"id": 1, "is_deleted": 1, "daijin_id": "33"}})
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert remote.calls == []


# ---------- error de BD tras Dajin OK ----------

def test_db_error_after_dajin_ok_returns_500_with_daijin_id(monkeypatch):
    # Dajin ya borró pero el update local falla: 500 y el mensaje DEBE incluir el
    # daijin_id para el rescate manual (regla del proyecto).
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})

    def boom(db, table, rid, data):
        raise RuntimeError("mysql down")

    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, vdel, store, remote)
    monkeypatch.setattr(vdel, "update", boom)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 500
    assert "33" in resp["body"]


# ---------- formato del 202 (contrato UX) ----------

def test_pending_delete_body_contract(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})
    remote = FakeRemote((TRANSIENT, "timeout x3"))
    _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    body = json.loads(resp["body"])
    assert body["status"] == "deleting"
    assert body["reason"] == "timeout x3"
    assert body["data"]["is_deleted"] == 1
