import pytest

from functions.sensors import bulk_sync_worker as mod


class FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeStore:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}

    def get_in(self, db, table, field, values, columns="*"):
        vals = set(values)
        return [dict(r) for r in self.rows.values() if r.get(field) in vals]

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])


class FakeSmartTyre:
    """Resolves every code except the ones listed in `failing`."""

    def __init__(self, failing=None):
        self.failing = set(failing or [])

    def get(self, path, params):
        code = params["sensorCode"]
        if code in self.failing:
            raise ConnectionError("platform timeout")
        return {"records": [{"id": int(code[-1]) + 1000}]}

    def post(self, path, body):
        return "Success"


class FakeContext:
    def __init__(self, remaining_ms=800_000):
        self.remaining_ms = remaining_ms
        self.aws_request_id = "test"

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


def _rows(n):
    return [
        {"id": i, "sensorCode": f"AAAAAAAAAAA{i}", "daijin_id": None,
         "status": "registering", "is_deleted": 0, "company_id": 100}
        for i in range(1, n + 1)
    ]


@pytest.fixture
def wire(monkeypatch):
    def setup(rows, st, remaining_ms=800_000):
        store = FakeStore(rows)
        reinvokes = []

        def fake_reinvoke(ids, pass_num, actor):
            reinvokes.append({"ids": sorted(ids), "pass": pass_num, "actor": actor})
            return True

        monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
        monkeypatch.setattr(mod, "get_in", store.get_in)
        monkeypatch.setattr(mod, "update", store.update)
        monkeypatch.setattr(mod, "audit", lambda *a, **k: None)
        monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)
        monkeypatch.setattr(mod, "_reinvoke", fake_reinvoke)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        return store, reinvokes, FakeContext(remaining_ms)

    return setup


def test_resolves_whole_batch(wire):
    store, reinvokes, ctx = wire(_rows(6), FakeSmartTyre())

    out = mod.handler({"ids": [1, 2, 3, 4, 5, 6], "pass": 1}, ctx)

    assert out == {"status": "ok", "resolved": 6, "pending": 0}
    assert all(r["status"] == "active" and r["daijin_id"] for r in store.rows.values())
    assert reinvokes == []


def test_skips_rows_already_active_or_deleted(wire):
    rows = _rows(3)
    rows[0]["status"] = "active"         # already active -> el filtro la excluye
    rows[0]["daijin_id"] = "99"
    rows[1]["is_deleted"] = 1            # deleted while queued
    store, reinvokes, ctx = wire(rows, FakeSmartTyre())

    out = mod.handler({"ids": [1, 2, 3]}, ctx)

    assert out["resolved"] == 1
    assert store.rows[1]["daijin_id"] == "99"  # untouched


def test_una_fila_con_daijin_id_se_activa_SOLO_si_la_plataforma_lo_confirma(wire, monkeypatch):
    """Limbo: la fila quedó 'registering' pero YA tiene daijin_id (el flip local a
    'active' falló en su momento).

    Antes se activaba sin preguntar, confiando en el campo local. Eso rompe el
    invariante del PR #42: un activo se activa **solo con confirmación de la
    plataforma**. El id guardado puede estar mal, o el activo puede haberse
    borrado allá, y quedaría una fila 'active' apuntando a nada.
    """
    preguntado = []
    monkeypatch.setattr(mod, "confirm_on_platform",
                        lambda row, recurso, **kw: (preguntado.append(recurso), "555")[1])

    rows = _rows(1)
    rows[0]["daijin_id"] = "555"
    store, reinvokes, ctx = wire(rows, FakeSmartTyre())

    out = mod.handler({"ids": [1]}, ctx)

    assert preguntado, "se activó sin confirmar contra la plataforma"
    assert out["resolved"] == 1
    assert store.rows[1]["status"] == "active"
    assert store.rows[1]["daijin_id"] == "555"
    assert reinvokes == []


def test_si_la_plataforma_no_lo_reconoce_la_fila_NO_se_activa(wire, monkeypatch):
    """El id guardado no corresponde a nada allá. Lo correcto es volver a
    registrarlo por la llave natural, no dar por buena la fila."""
    from shared.activation import NotOnPlatform

    monkeypatch.setattr(mod, "confirm_on_platform",
                        lambda row, recurso, **kw: (_ for _ in ()).throw(NotOnPlatform("no está")))

    rows = _rows(1)
    rows[0]["daijin_id"] = "id-basura"
    store, reinvokes, ctx = wire(rows, FakeSmartTyre())

    mod.handler({"ids": [1]}, ctx)

    # Se registró de nuevo: el id que queda es el que devolvió la plataforma,
    # no el basura que teníamos guardado.
    assert store.rows[1]["daijin_id"] != "id-basura"


def test_failed_rows_are_retried_as_next_pass(wire):
    store, reinvokes, ctx = wire(_rows(4), FakeSmartTyre(failing={"AAAAAAAAAAA2"}))

    out = mod.handler({"ids": [1, 2, 3, 4], "pass": 1, "actor": "cesar@quinta.tech"}, ctx)

    assert out["status"] == "retrying"
    assert out["resolved"] == 3
    assert reinvokes == [{"ids": [2], "pass": 2, "actor": "cesar@quinta.tech"}]


def test_gives_up_after_max_passes(wire):
    store, reinvokes, ctx = wire(_rows(1), FakeSmartTyre(failing={"AAAAAAAAAAA1"}))

    out = mod.handler({"ids": [1], "pass": mod.MAX_PASSES}, ctx)

    assert out == {"status": "ok", "resolved": 0, "pending": 1}
    assert reinvokes == []  # exhausted; row stays `registering` for re-import
    assert store.rows[1]["status"] == "registering"


def test_out_of_time_chains_same_pass(wire):
    # SAFETY_MS threshold hit before the first chunk -> everything is handed
    # to a fresh invocation on the SAME pass (no attempt burned).
    store, reinvokes, ctx = wire(_rows(6), FakeSmartTyre(), remaining_ms=10_000)

    out = mod.handler({"ids": [1, 2, 3, 4, 5, 6], "pass": 2}, ctx)

    assert out["status"] == "continued"
    assert reinvokes == [{"ids": [1, 2, 3, 4, 5, 6], "pass": 2, "actor": "system"}]


def test_auth_failure_backs_off_and_retries(wire, monkeypatch):
    store, reinvokes, ctx = wire(_rows(2), FakeSmartTyre())

    def boom():
        raise RuntimeError("no auth")

    monkeypatch.setattr(mod, "SmartTyreClient", boom)

    out = mod.handler({"ids": [1, 2], "pass": 1}, ctx)

    assert out["status"] == "auth_failed"
    assert reinvokes == [{"ids": [1, 2], "pass": 2, "actor": "system"}]
