"""Tests del reintento de sincronización (POST /sensors/resync y /tboxes/resync).

Mockea get_where/get_db y el cliente boto3 `lambda`. Verifica que:
  - solo selecciona filas 'registering' (via el WHERE que arma),
  - respeta el alcance por compañía (admin vs acotado) y los ids explícitos,
  - trocea en lotes y llama al worker una vez por lote con el payload correcto,
  - devuelve queued=0 (sin invocar) cuando no hay nada atascado.
"""
import json

import pytest

from functions.sensors import resync as sensors_resync
from functions.tboxes import resync as tboxes_resync


class FakeLambda:
    """Captura cada invoke al worker (mismo shape que boto3 client('lambda'))."""

    def __init__(self):
        self.invokes = []

    def invoke(self, **kwargs):
        self.invokes.append(kwargs)
        return {"StatusCode": 202}


@pytest.fixture
def wire(monkeypatch):
    def setup(mod, rows):
        captured = {}

        def fake_get_where(db, table, where_sql, params=(), limit=200):
            captured["table"] = table
            captured["where_sql"] = where_sql
            captured["params"] = list(params)
            captured["limit"] = limit
            return [dict(r) for r in rows]

        fake_lambda = FakeLambda()
        monkeypatch.setattr(mod, "get_db", lambda: object())
        monkeypatch.setattr(mod, "get_where", fake_get_where)
        monkeypatch.setattr(mod.boto3, "client", lambda name: fake_lambda)
        monkeypatch.setenv("BULK_SYNC_FUNCTION", "svc-dev-worker")
        return captured, fake_lambda

    return setup


def _event(body=None, actor=None):
    ev = {"body": json.dumps(body) if body is not None else None}
    if actor:
        ev["headers"] = {"X-Actor": actor}
    return ev


def _rows(ids, company_id=100):
    return [{"id": i, "status": "registering", "company_id": company_id} for i in ids]


# --- selección: solo 'registering' -----------------------------------------

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_selects_only_registering_not_deleted(mod, wire):
    captured, _ = wire(mod, _rows([1, 2, 3]))

    mod.handler(_event({}), None)

    assert "status = %s" in captured["where_sql"]
    assert "is_deleted IS NULL OR is_deleted = 0" in captured["where_sql"]
    assert captured["params"][0] == "registering"


# --- alcance por compañía ----------------------------------------------------

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_scoped_company_filters_by_company(mod, wire):
    captured, _ = wire(mod, _rows([1, 2]))

    mod.handler(_event({"company_id": 100}), None)

    assert "company_id = %s" in captured["where_sql"]
    assert captured["params"] == ["registering", 100]


@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_admin_company_has_no_company_filter(mod, wire):
    captured, _ = wire(mod, _rows([1, 2]))

    # company 2 = admin -> alcance global, sin filtro company_id
    mod.handler(_event({"company_id": 2}), None)

    assert "company_id = %s" not in captured["where_sql"]
    assert captured["params"] == ["registering"]


@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_omitted_company_is_global(mod, wire):
    captured, _ = wire(mod, _rows([1, 2]))

    mod.handler(_event({}), None)

    assert "company_id = %s" not in captured["where_sql"]
    assert captured["params"] == ["registering"]


# --- ids explícitos ----------------------------------------------------------

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_explicit_ids_added_to_where(mod, wire):
    captured, _ = wire(mod, _rows([5, 6]))

    mod.handler(_event({"ids": [5, 6]}), None)

    assert "id IN (%s, %s)" in captured["where_sql"]
    assert captured["params"] == ["registering", 5, 6]


@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_scope_and_ids_combined(mod, wire):
    captured, _ = wire(mod, _rows([5]))

    mod.handler(_event({"company_id": 100, "ids": [5]}), None)

    assert captured["params"] == ["registering", 100, 5]
    assert "company_id = %s" in captured["where_sql"]
    assert "id IN (%s)" in captured["where_sql"]


# --- troceo + invocación del worker -----------------------------------------

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_chunks_into_batches_and_invokes_worker_per_chunk(mod, wire):
    ids = list(range(1, 701))  # 700 -> 3 lotes (300, 300, 100)
    captured, fake_lambda = wire(mod, _rows(ids))

    resp = mod.handler(_event({}, actor="cesar@quinta.tech"), None)
    body = json.loads(resp["body"])

    assert resp["statusCode"] == 200
    assert body["queued"] == 700
    assert body["batches"] == 3
    assert len(fake_lambda.invokes) == 3

    total = []
    for inv in fake_lambda.invokes:
        assert inv["FunctionName"] == "svc-dev-worker"
        assert inv["InvocationType"] == "Event"
        payload = json.loads(inv["Payload"].decode())
        assert payload["pass"] == 1
        assert payload["actor"] == "cesar@quinta.tech"
        assert set(payload) == {"ids", "pass", "actor"}
        assert len(payload["ids"]) <= mod.CHUNK_SIZE
        total.extend(payload["ids"])
    assert sorted(total) == ids  # cada id encolado exactamente una vez


@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_single_chunk_payload_shape(mod, wire):
    captured, fake_lambda = wire(mod, _rows([1, 2, 3]))

    resp = mod.handler(_event({}), None)
    body = json.loads(resp["body"])

    assert body["queued"] == 3
    assert body["batches"] == 1
    assert "message" in body
    assert len(fake_lambda.invokes) == 1
    payload = json.loads(fake_lambda.invokes[0]["Payload"].decode())
    assert payload["ids"] == [1, 2, 3]
    assert payload["pass"] == 1
    assert payload["actor"] == "resync"  # default cuando no hay X-Actor


# --- nada atascado -----------------------------------------------------------

@pytest.mark.parametrize("mod", [sensors_resync, tboxes_resync])
def test_nothing_stuck_returns_queued_zero_no_invoke(mod, wire):
    captured, fake_lambda = wire(mod, [])

    resp = mod.handler(_event({}), None)
    body = json.loads(resp["body"])

    assert resp["statusCode"] == 200
    assert body["queued"] == 0
    assert body["batches"] == 0
    assert fake_lambda.invokes == []  # ningún worker disparado


# --- tabla correcta por tipo -------------------------------------------------

def test_sensors_hits_sensors_table(wire):
    captured, _ = wire(sensors_resync, _rows([1]))
    sensors_resync.handler(_event({}), None)
    assert captured["table"] == "sensors"  # TABLE_PREFIX vacío en test


def test_tboxes_hits_tboxes_table(wire):
    captured, _ = wire(tboxes_resync, _rows([1]))
    tboxes_resync.handler(_event({}), None)
    assert captured["table"] == "tboxes"
