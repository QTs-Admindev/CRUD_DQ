"""La asignación de paquete responde 202 y monta en segundo plano (assign_worker)."""
import json
from collections import defaultdict

import pytest

from functions.packages import assign as passign
from functions.packages import assign_worker as worker
from functions.reconciliation import reconcile
from tests.test_packages import FakeDB, Store, _assign_event, _seed_for_assign, _wire_assign


def _resp(code, body):
    return {"statusCode": code, "body": json.dumps(body)}


@pytest.fixture
def env(monkeypatch):
    store, db, calls = Store(), FakeDB(), defaultdict(int)
    _seed_for_assign(store)
    _wire_assign(monkeypatch, store, db, calls)
    invoked = []

    def fake_claim(db_, pid, unit_id, total):
        pkg = store.tables["packages"][pid]
        if pkg["status"] not in passign.ASSIGNABLE:
            return False
        pkg.update(status="assigning", unit_id=unit_id, assign_done=0,
                   assign_total=total, assign_error=None)
        return True

    def bind_tire(ev, ctx):
        b = json.loads(ev["body"])
        calls["bind_tire"] += 1
        store.tables["tires"][b["tire_id"]].update(
            unit_id=int(ev["pathParameters"]["id"]), mount_position=b["mount_position"], is_mounted=1)
        return _resp(200, {})

    def bind_sensor(ev, ctx):
        calls["bind_sensor"] += 1
        store.tables["tires"][int(ev["pathParameters"]["id"])]["sensor_id"] = json.loads(ev["body"])["sensor_id"]
        return _resp(200, {})

    monkeypatch.setattr(passign, "bind_tire_handler", bind_tire)
    monkeypatch.setattr(passign, "bind_sensor_handler", bind_sensor)
    monkeypatch.setattr(passign, "_claim", fake_claim)
    monkeypatch.setattr(passign, "_invoke_worker",
                        lambda pid, uid, actor: invoked.append((pid, uid, actor)) or True)
    monkeypatch.setattr(worker, "get_db", lambda: db)
    monkeypatch.setattr(worker, "get_by_id", store.get_by_id)
    monkeypatch.setattr(worker, "update", store.update)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    return store, calls, invoked


def _pkg(store):
    return store.tables["packages"][1]


def test_el_endpoint_responde_202_y_lanza_el_worker(env):
    store, calls, invoked = env
    ev = {**_assign_event(1, 1), "headers": {"X-Actor": "ops@quinta.tech"}}
    resp = passign.handler(ev, None)
    assert resp["statusCode"] == 202
    assert json.loads(resp["body"])["data"]["status"] == "assigning"
    assert invoked == [(1, 1, "ops@quinta.tech")]
    assert calls["tire_create"] == 0  # no monta nada dentro de la petición
    assert _pkg(store)["assign_total"] == 2


def test_no_lanza_dos_veces(env):
    store, calls, invoked = env
    passign.handler(_assign_event(1, 1), None)
    assert passign.handler(_assign_event(1, 1), None)["statusCode"] == 409
    assert len(invoked) == 1


def test_si_no_arranca_el_worker_queda_para_reintentar(env, monkeypatch):
    store, calls, invoked = env
    monkeypatch.setattr(passign, "_invoke_worker", lambda *a: False)
    resp = passign.handler(_assign_event(1, 1), None)
    assert json.loads(resp["body"])["data"]["status"] == "assign_failed"
    assert _pkg(store)["assign_error"]


def test_reintento_solo_en_la_misma_unidad(env):
    store, calls, invoked = env
    _pkg(store).update(status="assign_failed", unit_id=1)
    store.seed("units", {"id": 2, "unit_catalog_id": 209, "company_id": 5})
    assert passign.handler(_assign_event(1, 2), None)["statusCode"] == 409
    assert passign.handler(_assign_event(1, 1), None)["statusCode"] == 202


def test_el_worker_monta_y_reporta_avance(env):
    store, calls, invoked = env
    passign.handler(_assign_event(1, 1), None)
    assert worker.handler({"package_id": 1, "unit_id": 1}, None) == {"assigned": True}
    pkg = _pkg(store)
    assert pkg["status"] == "assigned" and pkg["assign_done"] == 2
    assert calls["bind_sensor"] == 2 and calls["bind_tbox"] == 1


def test_el_worker_reintenta_fallas_de_la_plataforma(env, monkeypatch):
    store, calls, invoked = env
    passign.handler(_assign_event(1, 1), None)
    fallas = iter([_resp(502, {"error": "plataforma"})])

    def flaky_bind_tbox(ev, ctx):
        return next(fallas, _resp(200, {}))

    monkeypatch.setattr(passign, "bind_tbox_handler", flaky_bind_tbox)
    assert worker.handler({"package_id": 1, "unit_id": 1}, None) == {"assigned": True}
    assert calls["tire_create"] == 2 and calls["bind_sensor"] == 2  # el reintento no repite


def test_el_worker_deja_el_motivo_si_falla(env, monkeypatch):
    store, calls, invoked = env
    passign.handler(_assign_event(1, 1), None)
    monkeypatch.setattr(passign, "bind_sensor_handler",
                        lambda ev, ctx: _resp(422, {"error": "Sensor de otra compañía"}))
    worker.handler({"package_id": 1, "unit_id": 1}, None)
    pkg = _pkg(store)
    assert pkg["status"] == "assign_failed"
    assert pkg["assign_error"] == "Sensor de otra compañía"
    assert pkg["assign_done"] == 0


def test_el_worker_ignora_paquetes_que_no_estan_asignandose(env):
    store, calls, invoked = env
    assert worker.handler({"package_id": 1, "unit_id": 1}, None)["skipped"] is True
    assert calls["tire_create"] == 0


def test_el_reconciliador_suelta_los_atorados(monkeypatch):
    store, db = Store(), FakeDB()
    store.seed("packages", {"id": 1, "status": "assigning", "updated_at": 0})
    store.seed("packages", {"id": 2, "status": "assigning", "updated_at": 10**13})
    monkeypatch.setattr(reconcile, "get_where", lambda db_, table, where, params, limit: [
        dict(r) for r in store.tables["packages"].values()
        if r["status"] == "assigning" and r["updated_at"] < params[0]])
    monkeypatch.setattr(reconcile, "update", store.update)
    reconcile._sweep_stale_assigning(db)
    assert store.tables["packages"][1]["status"] == "assign_failed"
    assert store.tables["packages"][2]["status"] == "assigning"
