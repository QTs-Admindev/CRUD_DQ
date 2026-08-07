"""Barrido C de reconciliación: re-liga en la plataforma las relaciones que localmente
existen pero allá no (Qbox/llanta/sensor), verificando por read-back. Sana divergencias
existentes (p.ej. un Qbox fantasma) sin que nadie re-cree nada.
"""
from functions.reconciliation import reconcile
from shared.smarttyre import verify


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeSt:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))

    def get(self, path, params=None):
        return {"records": []}


def _rows_for(where_sql, unit=None, tire=None):
    """Devuelve filas solo para el barrido correspondiente (matcher por prioridad)."""
    if "sensor_id IS NOT NULL" in where_sql:
        return [dict(tire)] if tire else []
    if "tbox_id IS NOT NULL" in where_sql:
        return [dict(unit)] if unit else []
    if "unit_id IS NOT NULL" in where_sql:
        return [dict(tire)] if tire else []
    return []


def _wire(monkeypatch, *, unit=None, tire=None, store=None, tbox_bound_seq=None,
          tyre_on_seq=None, sensor_on_seq=None):
    st = FakeSt()
    updates, audits = [], []
    monkeypatch.setattr(reconcile, "get_db", lambda: FakeDB())
    monkeypatch.setattr(reconcile, "SmartTyreClient", lambda: st)
    monkeypatch.setattr(reconcile, "get_where",
                        lambda db, table, where, params=(), limit=100: _rows_for(where, unit, tire))
    monkeypatch.setattr(reconcile, "get_by_id",
                        lambda db, table, rid: dict(store[table][rid]) if store and rid in store.get(table, {}) else None)
    monkeypatch.setattr(reconcile, "update",
                        lambda db, table, rid, data: updates.append((table, rid, data)))
    monkeypatch.setattr(reconcile, "audit", lambda *a, **k: audits.append(k))
    monkeypatch.setattr(reconcile, "_dajin_type", lambda catalog: (1, "40"))
    monkeypatch.setattr(reconcile, "resolve_or_heal",
                        lambda st, *, stored_id, **k: (stored_id or "NEW", False))
    monkeypatch.setattr(reconcile, "platform_bind_sensor", lambda *a, **k: None)

    def _seq(seq, default):
        if seq is None:
            return lambda *a, **k: default
        it = iter(seq)
        return lambda *a, **k: next(it)

    monkeypatch.setattr(verify, "tbox_bound", _seq(tbox_bound_seq, True))
    monkeypatch.setattr(verify, "tyre_on_vehicle", _seq(tyre_on_seq, True))
    monkeypatch.setattr(verify, "sensor_on_tyre", _seq(sensor_on_seq, True))
    return st, updates, audits


UNIT = {"id": 1903, "daijin_id": "35094", "unit_catalog_id": 5, "company_id": 133,
        "tbox_id": 601, "status": "active"}
TBOX = {"id": 601, "tboxCode": "08927234DC91", "daijin_id": "35374", "company_id": 133}
CATALOG = {"id": 5, "name": "truck", "type": "motive", "d_id": 7}
# 'units' está en el store porque el barrido re-lee la fila primaria antes de re-ligar.
STORE = {"units": {1903: UNIT}, "tboxes": {601: TBOX}, "unit_catalog": {5: CATALOG}}


def test_qbox_rebound_when_diverged(monkeypatch):
    # verify: False (divergente) y luego True (confirmado tras re-ligar).
    st, updates, audits = _wire(monkeypatch, unit=UNIT, store=STORE,
                                tbox_bound_seq=[False, True])
    out = reconcile.handler({}, None)
    assert out["rebound"] == 1 and out["binding_pending"] == 0
    assert any(p == "/smartyre/openapi/vehicle/update" and b["tboxCode"] == "08927234DC91"
               for p, b in st.posts)
    assert any(a.get("action") == "reconcile" and a.get("result") == "success" for a in audits)


def test_qbox_left_pending_when_still_not_confirmed(monkeypatch):
    # La plataforma sigue sin reflejar el bind aun tras re-crear+re-ligar (p.ej. la 133 que
    # el proveedor vuelve a borrar) -> pending, NUNCA falso éxito.
    st, updates, audits = _wire(monkeypatch, unit=UNIT, store=STORE,
                                tbox_bound_seq=[False, False])
    out = reconcile.handler({}, None)
    assert out["rebound"] == 0 and out["binding_pending"] == 1
    assert any(a.get("result") == "pending" for a in audits)


def test_qbox_already_bound_is_skipped(monkeypatch):
    st, updates, audits = _wire(monkeypatch, unit=UNIT, store=STORE, tbox_bound_seq=[True])
    out = reconcile.handler({}, None)
    assert out["rebound"] == 0 and out["binding_pending"] == 0
    assert st.posts == []                                  # no re-ligó nada


TIRE = {"id": 10, "daijin_id": "P", "unit_id": 1903, "is_mounted": 1,
        "axle_index": 2, "wheel_index": 4, "company_id": 133, "folio": "F", "sensor_id": None}


def test_tyre_rebound_when_diverged(monkeypatch):
    # 'tires' en el store porque el barrido re-lee la llanta antes de re-montar.
    store = {"units": {1903: UNIT}, "tires": {10: TIRE}, "unit_catalog": {5: CATALOG}}
    st, updates, audits = _wire(monkeypatch, tire=TIRE, store=store,
                                tyre_on_seq=[False, True])
    out = reconcile.handler({}, None)
    assert out["rebound"] == 1
    assert any(p == "/smartyre/openapi/vehicle/tyre/bind" for p, b in st.posts)
