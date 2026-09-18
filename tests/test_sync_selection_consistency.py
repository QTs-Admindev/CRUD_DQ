"""Las tres piezas que deciden "esto falta sincronizar" tienen que coincidir.

Son tres y viven en archivos distintos: el endpoint de reintento arma un WHERE, el
worker al que ese endpoint encola filtra en Python, y el cron barre con otro WHERE. Si
una usa `status` y otra `daijin_id`, el reintento responde `queued: N` y el worker
descarta esas mismas filas en silencio: el operador ve que "se encoló" y nada ocurre.

Estas pruebas fijan el criterio en los tres lugares a la vez, que es justo lo que la
suite no cubría cuando el worker se quedó filtrando por status.
"""
import json

import pytest

from functions.reconciliation import reconcile
from functions.sensors import bulk_sync_worker as sensors_worker
from functions.sensors import resync as sensors_resync
from functions.tboxes import bulk_sync_worker as tboxes_worker
from functions.tboxes import resync as tboxes_resync


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


# --------------------------------------------------- el WHERE del endpoint de reintento

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_resync_selects_by_missing_platform_id(mod, monkeypatch):
    captured = {}

    def fake_get_where(db, table, where_sql, params=(), limit=200, order="ASC"):
        captured["where"] = where_sql
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_where", fake_get_where)
    mod.handler({"body": json.dumps({})}, None)

    assert "daijin_id IS NULL" in captured["where"]
    assert "status" not in captured["where"]


# ------------------------------------------------------------- el filtro del worker

@pytest.mark.parametrize("mod, table", [(sensors_worker, "sensors"),
                                        (tboxes_worker, "tboxes")])
def test_worker_accepts_what_resync_queues(mod, table, monkeypatch):
    """El worker debe tomar las mismas filas que el resync encola.

    Caso que se escapaba: una fila marcada 'active' a mano, sin id en la plataforma. El
    resync la selecciona; si el worker la descarta por status, el reintento no sirve de
    nada y nadie se entera.
    """
    rows = [
        {"id": 1, "status": "active", "daijin_id": None, "is_deleted": 0},      # marcada a mano
        {"id": 2, "status": "registering", "daijin_id": None, "is_deleted": 0},  # el caso normal
        {"id": 3, "status": "registering", "daijin_id": "55", "is_deleted": 0},  # limbo: falta el flip local
        {"id": 4, "status": "active", "daijin_id": "56", "is_deleted": 0},       # sana: fuera
        {"id": 5, "status": "registering", "daijin_id": None, "is_deleted": 1},  # borrada: fuera
    ]
    seen = {}

    class Platform:
        def get(self, *a, **k):
            return {"records": []}

        def post(self, *a, **k):
            return "Success"

    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_in", lambda db, table, field, ids: [dict(r) for r in rows])
    monkeypatch.setattr(mod, "SmartTyreClient", lambda: Platform())
    # La fila que ya trae `daijin_id` YA NO se activa confiando en el campo local:
    # el worker confirma contra la plataforma primero (ver
    # test_sync_confirma_ambos_lados.py). Aquí se simula esa confirmación, porque
    # lo que esta prueba mide es la SELECCIÓN de filas, no la confirmación.
    monkeypatch.setattr(mod, "confirm_on_platform",
                        lambda row, recurso, **kw: row.get("daijin_id"))
    monkeypatch.setattr(mod, "update", lambda db, table, rid, data: seen.setdefault("updates", []).append(rid))
    monkeypatch.setattr(mod, "audit", lambda *a, **k: None)

    captured = {}
    original_get_in = mod.get_in

    def spy_get_in(db, table_name, field, ids):
        captured["rows"] = original_get_in(db, table_name, field, ids)
        return captured["rows"]

    monkeypatch.setattr(mod, "get_in", spy_get_in)

    out = mod.handler({"ids": [1, 2, 3, 4, 5], "pass": 1}, None)

    # Se toman las TRES filas que aún necesitan algo (1, 2 y 3) y solo esas: la sana y la
    # borrada quedan fuera. `status` puede ser "ok" o "retrying" según queden pendientes;
    # lo que importa es cuántas entraron al lote.
    procesadas = out.get("resolved", 0) + out.get("pending", 0)
    assert procesadas == 3, f"el worker no tomó las mismas filas que encola el resync: {out}"
    # La fila en limbo (id en la plataforma, status 'registering') se resuelve en local.
    assert 3 in seen.get("updates", []), "no se recuperó la fila que solo necesitaba el flip local"


# ------------------------------------------------------------------ el WHERE del cron

def test_reconcile_sweep_selects_by_missing_platform_id_newest_first(monkeypatch):
    captured = []

    def fake_get_where(db, table, where_sql, params=(), limit=100, order="ASC"):
        captured.append((where_sql, order))
        return []

    monkeypatch.setattr(reconcile, "get_db", lambda: FakeDB())
    monkeypatch.setattr(reconcile, "SmartTyreClient", lambda: object())
    monkeypatch.setattr(reconcile, "get_where", fake_get_where)
    monkeypatch.setattr(reconcile, "attempt_delete", lambda *a, **k: (reconcile.DONE, None))
    reconcile.handler({}, None)

    sweeps = [(w, o) for w, o in captured if "daijin_id IS NULL" in w]
    assert sweeps, "el barrido de creates ya no selecciona por daijin_id IS NULL"
    # Lo más reciente primero: el selector abarca también filas viejas que quizá nunca
    # se resuelvan, y con orden ascendente esas se comerían el cupo en cada corrida.
    assert all(order == "DESC" for _, order in sweeps)
