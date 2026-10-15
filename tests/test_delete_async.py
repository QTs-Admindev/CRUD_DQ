"""DELETE /vehicles/{id} responde 202 y borra en segundo plano (delete_worker)."""
import json

import pytest

from functions.reconciliation import reconcile
from functions.vehicles import delete as vdel
from functions.vehicles import delete_worker as worker


def _resp(code, body):
    return {"statusCode": code, "body": json.dumps(body)}


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


@pytest.fixture
def env(monkeypatch):
    units = {1: {"id": 1, "is_deleted": 0, "deleting_at": None, "delete_error": None}}
    invoked = []

    def get_by_id(db, table, rid):
        r = units.get(rid)
        return dict(r) if r else None

    def update(db, table, rid, data):
        units[rid].update(data)
        return dict(units[rid])

    def claim(db, rid):
        u = units[rid]
        if u["is_deleted"] or u["deleting_at"]:
            return False
        u.update(deleting_at=123, delete_error=None)
        return True

    for mod in (vdel, worker):
        monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
        monkeypatch.setattr(mod, "get_by_id", get_by_id)
        monkeypatch.setattr(mod, "update", update)
    monkeypatch.setattr(vdel, "_claim", claim)
    monkeypatch.setattr(vdel, "_invoke_worker", lambda rid, actor: invoked.append((rid, actor)) or True)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    return units, invoked


def _ev(rid=1, actor=None):
    return {"pathParameters": {"id": str(rid)}, "headers": {"X-Actor": actor} if actor else {}}


def test_responde_202_y_lanza_el_worker(env):
    units, invoked = env
    resp = vdel.handler(_ev(actor="ops@quinta.tech"), None)
    assert resp["statusCode"] == 202
    assert invoked == [(1, "ops@quinta.tech")]
    assert units[1]["deleting_at"] and not units[1]["is_deleted"]


def test_no_lanza_dos_veces(env):
    units, invoked = env
    vdel.handler(_ev(), None)
    assert vdel.handler(_ev(), None)["statusCode"] == 202
    assert len(invoked) == 1


def test_si_no_arranca_el_worker_suelta_la_marca(env, monkeypatch):
    units, invoked = env
    monkeypatch.setattr(vdel, "_invoke_worker", lambda *a: False)
    assert vdel.handler(_ev(), None)["statusCode"] == 503
    assert units[1]["deleting_at"] is None


def test_ya_borrada_es_idempotente(env):
    units, invoked = env
    units[1]["is_deleted"] = 1
    assert vdel.handler(_ev(), None)["statusCode"] == 200
    assert invoked == []


def test_el_worker_borra(env, monkeypatch):
    units, invoked = env
    vdel.handler(_ev(), None)
    monkeypatch.setattr(worker, "delete_now", lambda ev, ctx: _resp(200, {"id": 1}))
    assert worker.handler({"unit_id": 1}, None)["deleted"] is True


def test_el_worker_reintenta_fallas_de_la_plataforma(env, monkeypatch):
    units, invoked = env
    vdel.handler(_ev(), None)
    respuestas = iter([_resp(502, {"error": "plataforma"}), _resp(200, {"id": 1})])
    monkeypatch.setattr(worker, "delete_now", lambda ev, ctx: next(respuestas))
    assert worker.handler({"unit_id": 1}, None)["deleted"] is True


def test_el_worker_deja_el_motivo_si_falla(env, monkeypatch):
    units, invoked = env
    vdel.handler(_ev(), None)
    monkeypatch.setattr(worker, "delete_now", lambda ev, ctx: _resp(409, {"error": "Tiene un Qbox vinculado"}))
    worker.handler({"unit_id": 1}, None)
    assert units[1]["deleting_at"] is None
    assert units[1]["delete_error"] == "Tiene un Qbox vinculado"


def test_el_worker_ignora_unidades_sin_marca(env, monkeypatch):
    units, invoked = env
    called = []
    monkeypatch.setattr(worker, "delete_now", lambda ev, ctx: called.append(1))
    assert worker.handler({"unit_id": 1}, None)["skipped"] is True
    assert called == []


def test_el_reconciliador_suelta_los_borrados_atorados(monkeypatch):
    units = {1: {"id": 1, "deleting_at": 0, "is_deleted": 0}, 2: {"id": 2, "deleting_at": 10**13, "is_deleted": 0}}
    monkeypatch.setattr(reconcile, "get_where", lambda db, table, where, params, limit: [
        dict(u) for u in units.values() if u["deleting_at"] is not None and u["deleting_at"] < params[0]])
    monkeypatch.setattr(reconcile, "update", lambda db, table, rid, data: units[rid].update(data))
    reconcile._sweep_stale_deleting(FakeDB())
    assert units[1]["deleting_at"] is None and units[1]["delete_error"]
    assert units[2]["deleting_at"] == 10**13
