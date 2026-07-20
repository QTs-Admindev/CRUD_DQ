from functions.reconciliation import reconcile


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


def _make_get_where(reg=None, dele=None):
    reg = reg or {}
    dele = dele or {}

    def gw(db, table, where_sql, params=(), limit=100):
        if "status" in where_sql:                       # sweep de registering
            return list(reg.get(table, []))
        if "daijin_id IS NOT NULL" in where_sql:        # sweep de borrados pendientes
            return list(dele.get(table, []))
        return []
    return gw


def _wire(monkeypatch, get_where, find_id, attempt):
    updates = []
    monkeypatch.setattr(reconcile, "get_db", lambda: FakeDB())
    monkeypatch.setattr(reconcile, "SmartTyreClient", lambda: object())
    monkeypatch.setattr(reconcile, "get_where", get_where)
    monkeypatch.setattr(reconcile, "_find_id", find_id)
    monkeypatch.setattr(reconcile, "attempt_delete", attempt)
    monkeypatch.setattr(reconcile, "update",
                        lambda db, table, rid, data: updates.append((table, rid, data)))
    return updates


def test_resolves_registering_create(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(reg={"units": [{"id": 1}]}),
        find_id=lambda st, path, key: "500",
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    out = reconcile.handler({}, None)
    assert out["resolved"] == 1
    assert updates[0] == ("units", 1, updates[0][2])
    assert updates[0][2]["daijin_id"] == "500"
    assert updates[0][2]["status"] == "active"


def test_completes_pending_delete(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(dele={"tires": [{"id": 2, "daijin_id": "99"}]}),
        find_id=lambda *a: None,
        attempt=lambda resource, did, *a, **k: (reconcile.DONE, None),
    )
    out = reconcile.handler({}, None)
    assert out["deleted"] == 1
    assert updates[0][2]["daijin_id"] is None       # _clear_daijin


def test_guard_but_already_gone_is_cleared(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(dele={"sensors": [{"id": 3, "daijin_id": "7", "sensorCode": "AA"}]}),
        find_id=lambda *a: None,                     # ya no existe en Dajin
        attempt=lambda *a, **k: (reconcile.GUARD, "not found"),
    )
    out = reconcile.handler({}, None)
    assert out["deleted"] == 1
    assert updates[0][2]["daijin_id"] is None


def test_real_guard_is_left_blocked(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(dele={"tires": [{"id": 4, "daijin_id": "8"}]}),
        find_id=lambda *a: "8",                      # sigue existiendo -> guard real
        attempt=lambda *a, **k: (reconcile.GUARD, "轮胎已绑定传感器"),
    )
    out = reconcile.handler({}, None)
    assert out["guard_blocked"] == 1
    assert updates == []                             # no se limpió nada


# ---------- transitorio: se deja para la próxima corrida ----------

def test_transient_delete_left_for_next_run(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(dele={"units": [{"id": 9, "daijin_id": "44"}]}),
        find_id=lambda *a: "44",
        attempt=lambda *a, **k: (reconcile.TRANSIENT, "timeout"),
    )
    out = reconcile.handler({}, None)
    assert out["deleted"] == 0 and out["guard_blocked"] == 0 and out["errors"] == 0
    assert updates == []                             # daijin_id intacto -> se reintenta


# ---------- registering aún no visible en Dajin: se salta sin tocar ----------

def test_registering_not_yet_in_dajin_is_skipped(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(reg={"sensors": [{"id": 5, "sensorCode": "AA"}]}),
        find_id=lambda *a: None,                     # todavía no propaga
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    out = reconcile.handler({}, None)
    assert out["resolved"] == 0 and out["errors"] == 0
    assert updates == []


# ---------- aislamiento: una fila que truena no tumba a las demás ----------

def test_error_in_one_row_does_not_stop_the_rest(monkeypatch):
    calls = {"n": 0}

    def flaky_find(st, path, key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")               # primera fila truena
        return "600"                                 # segunda resuelve

    updates = _wire(
        monkeypatch,
        _make_get_where(reg={"units": [{"id": 1}, {"id": 2}]}),
        find_id=flaky_find,
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    out = reconcile.handler({}, None)
    assert out["errors"] == 1
    assert out["resolved"] == 1                      # la segunda sí se procesó
    assert updates[0][1] == 2


def test_update_failure_counts_as_error(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(dele={"tboxes": [{"id": 6, "daijin_id": "12", "tboxCode": "BB"}]}),
        find_id=lambda *a: None,
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    monkeypatch.setattr(reconcile, "update",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    out = reconcile.handler({}, None)
    assert out["errors"] == 1 and out["deleted"] == 0


# ---------- auth de la OpenAPI caído: aborta con detalle, sin tocar nada ----------

def test_smarttyre_auth_failure_aborts(monkeypatch):
    monkeypatch.setattr(reconcile, "get_db", lambda: FakeDB())
    monkeypatch.setattr(reconcile, "SmartTyreClient",
                        lambda: (_ for _ in ()).throw(RuntimeError("auth 500")))
    out = reconcile.handler({}, None)
    assert "error" in out and "auth 500" in out["error"]


# ---------- multi-tabla: barre los 4 activos en una corrida ----------

def test_sweeps_multiple_tables_in_one_run(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(
            reg={"units": [{"id": 1}]},
            dele={"sensors": [{"id": 2, "daijin_id": "7", "sensorCode": "AA"}]},
        ),
        find_id=lambda st, path, key: "900" if "vehicle" in path else None,
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    out = reconcile.handler({}, None)
    assert out["resolved"] == 1 and out["deleted"] == 1
    tables = {u[0] for u in updates}
    assert tables == {"units", "sensors"}


# ---------- el status al re-resolver respeta el activo ----------

def test_resolved_tire_gets_business_status_new(monkeypatch):
    updates = _wire(
        monkeypatch,
        _make_get_where(reg={"tires": [{"id": 8}]}),
        find_id=lambda *a: "321",
        attempt=lambda *a, **k: (reconcile.DONE, None),
    )
    reconcile.handler({}, None)
    assert updates[0][2]["status"] == "new"          # llanta: status de negocio, no 'active'
