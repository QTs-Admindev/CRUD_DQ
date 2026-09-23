"""El vigía de sincronización: diagnostica, escribe solo cuando cambia algo, y nunca
toca el flujo (no escribe en sensors/tboxes ni da de alta nada en la plataforma)."""
import json

import pytest

from shared.activation import NotOnPlatform, PlatformUnavailable
from functions.reconciliation import sync_watcher as mod


class FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


class Store:
    """Tablas en memoria: sensors, tboxes y asset_audit_log."""

    def __init__(self, sensors=(), tboxes=(), audit_rows=()):
        self.tables = {"sensors": {r["id"]: dict(r) for r in sensors},
                       "tboxes": {r["id"]: dict(r) for r in tboxes}}
        self.audit = [dict(r, id=i + 1) for i, r in enumerate(audit_rows)]
        self.writes_to_assets = 0

    # get_where solo se usa con las consultas del vigía; se interpretan por forma.
    def get_where(self, db, table, where_sql, params=(), limit=200, order="ASC"):
        if table in self.tables:
            rows = [r for r in self.tables[table].values()
                    if not r.get("daijin_id") and not r.get("is_deleted")]
            return sorted(rows, key=lambda r: r["id"])[:limit]
        rows = list(reversed(self.audit)) if order == "DESC" else list(self.audit)
        atype = params[0]
        rows = [r for r in rows if r["asset_type"] == atype]
        if "natural_key LIKE" in where_sql:
            return [r for r in rows if str(r.get("natural_key", "")).startswith("bulk:giveup:")]
        if "actor <> %s" in where_sql:
            ids = set(params[2:])
            return [r for r in rows if r.get("actor") != params[1] and r.get("error")
                    and r.get("asset_id") in ids]
        return [r for r in rows if r.get("actor") == params[1]]

    def get_in(self, db, table, field, values, columns="*"):
        return [dict(r) for r in self.tables[table].values() if r.get(field) in set(values)]

    def audit_fn(self, db, event=None, context=None, **k):
        k["payload"] = json.dumps(k.get("payload")) if k.get("payload") is not None else None
        self.audit.append(dict(k, id=len(self.audit) + 1))


class Platform:
    """Doble de la plataforma: `tiene` = códigos dados de alta; `caida` = no responde."""

    def __init__(self, tiene=None, caida=False):
        self.tiene = tiene or {}
        self.caida = caida
        self.posts = []

    def post(self, *a, **k):  # el vigía nunca debe llamarlo
        self.posts.append(a)


@pytest.fixture
def wire(monkeypatch):
    def setup(store, platform):
        monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
        monkeypatch.setattr(mod, "get_where", store.get_where)
        monkeypatch.setattr(mod, "get_in", store.get_in)
        monkeypatch.setattr(mod, "audit", store.audit_fn)
        monkeypatch.setattr(mod, "SmartTyreClient", lambda: platform)

        def confirm(rec, resource, client=None, backoff=None):
            if client.caida:
                raise PlatformUnavailable("timeout")
            code = rec.get("sensorCode") or rec.get("tboxCode")
            if code in client.tiene:
                return client.tiene[code]
            raise NotOnPlatform(code)

        monkeypatch.setattr(mod, "confirm_on_platform", confirm)
    return setup


def _vigia(store):
    return [r for r in store.audit if r.get("actor") == mod.ACTOR]


def _diag(row):
    return json.loads(row["payload"])["diagnostico"]


SENSOR_537 = {"id": 5213, "sensorCode": "A4C1389BAB66", "company_id": 133, "daijin_id": None,
              "status": "registering", "batch_code": None}
SENSOR_INVALIDO = {"id": 125, "sensorCode": "A4C1383FE2C0123", "company_id": 2, "daijin_id": None}
AUDIT_537 = {"asset_type": "sensor", "asset_id": 5213, "actor": "system", "action": "create",
             "result": "pending", "error": "/smartyre/openapi/sensor/insert -> code 537: 传感器已存在"}
GIVEUP = {"asset_type": "sensor", "actor": "resync", "action": "create", "result": "failed",
          "natural_key": "bulk:giveup:2", "payload": json.dumps({"pending_ids": [125, 5213]})}


def test_diagnostica_los_dos_atorados_reales(wire):
    store = Store(sensors=[SENSOR_537, SENSOR_INVALIDO], audit_rows=[AUDIT_537, GIVEUP, GIVEUP])
    wire(store, Platform())

    mod.handler({}, None)

    filas = {r["asset_id"]: r for r in _vigia(store)}
    assert _diag(filas[5213]) == "ya_existe_en_otra_cuenta"
    assert filas[5213]["result"] == "failed"
    assert json.loads(filas[5213]["payload"])["rendiciones_del_worker"] == 2
    assert "537" in json.loads(filas[5213]["payload"])["ultimo_error_plataforma"]
    assert _diag(filas[125]) == "codigo_invalido"
    assert filas[125]["result"] == "failed"


def test_no_repite_si_nada_cambio(wire):
    store = Store(sensors=[SENSOR_537], audit_rows=[AUDIT_537])
    wire(store, Platform())

    mod.handler({}, None)
    mod.handler({}, None)
    mod.handler({}, None)

    assert len(_vigia(store)) == 1


def test_anota_cuando_se_resuelve(wire):
    store = Store(sensors=[dict(SENSOR_537)], audit_rows=[AUDIT_537])
    wire(store, Platform())
    mod.handler({}, None)

    store.tables["sensors"][5213].update(daijin_id="999", status="active")
    mod.handler({}, None)

    ultima = _vigia(store)[-1]
    assert ultima["result"] == "success" and _diag(ultima) == "resuelto"


def test_anota_cuando_se_borra_sin_sincronizar(wire):
    store = Store(sensors=[dict(SENSOR_INVALIDO)])
    wire(store, Platform())
    mod.handler({}, None)

    store.tables["sensors"][125]["is_deleted"] = 1
    mod.handler({}, None)

    assert _diag(_vigia(store)[-1]) == "borrado"


def test_en_la_plataforma_sin_id_local_es_pending(wire):
    store = Store(tboxes=[{"id": 9, "tboxCode": "10B41D30EA79", "company_id": 5, "daijin_id": None}])
    wire(store, Platform(tiene={"10B41D30EA79": "4711"}))

    mod.handler({}, None)

    fila = _vigia(store)[0]
    assert fila["asset_type"] == "tbox" and fila["result"] == "pending"
    assert _diag(fila) == "en_la_plataforma_sin_id_local"
    assert json.loads(fila["payload"])["daijin_en_plataforma"] == "4711"


def test_nunca_llego_a_la_plataforma_es_pending(wire):
    store = Store(sensors=[{"id": 7, "sensorCode": "AABBCCDDEEFF", "daijin_id": None, "batch_code": "L-1"}])
    wire(store, Platform())

    mod.handler({}, None)

    fila = _vigia(store)[0]
    assert _diag(fila) == "no_esta_en_la_plataforma" and fila["result"] == "pending"
    assert json.loads(fila["payload"])["lote"] == "L-1"


def test_con_la_plataforma_caida_no_escribe_nada_salvo_codigos_invalidos(wire):
    store = Store(sensors=[SENSOR_537, SENSOR_INVALIDO], audit_rows=[AUDIT_537])
    wire(store, Platform(caida=True))

    resumen = mod.handler({}, None)

    assert [r["asset_id"] for r in _vigia(store)] == [125]
    assert resumen["sin_plataforma"] == 1


def test_es_solo_observador(wire):
    antes = {5213: dict(SENSOR_537), 125: dict(SENSOR_INVALIDO)}
    store = Store(sensors=antes.values(), audit_rows=[AUDIT_537])
    plataforma = Platform()
    wire(store, plataforma)

    mod.handler({}, None)

    assert store.tables["sensors"] == {k: dict(v) for k, v in antes.items()}
    assert plataforma.posts == []


def test_por_construccion_no_puede_escribir_activos_ni_dar_de_alta():
    # Si alguien le agrega una escritura al vigía, esta prueba lo dice antes que producción.
    import inspect
    src = inspect.getsource(mod)
    for prohibido in ("import update", "insert(", "update(db", "resolve_or_create",
                      "resolve_or_heal", ".post(", "attempt_delete"):
        assert prohibido not in src, prohibido
