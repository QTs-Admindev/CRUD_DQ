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
        self.mounted = []           # tires que get_where devuelve (cascada del vehículo)
        self.pkg_sensors = []       # sensores del paquete (get_where por package_id)
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

    def get_where(self, db, table, where_sql, params=(), limit=200):
        # tdel (recuperación de paquete) lo usa para listar los sensores del paquete;
        # vdel lo usa para listar las llantas montadas de la unidad (cascada).
        if "package_id" in where_sql:
            return [dict(r) for r in self.pkg_sensors]
        return [dict(r) for r in self.mounted]


class FakeSmartTyre:
    """OpenAPI client stub: los deletes en cascada llaman unbind antes de borrar."""
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, path, body):
        if self.fail:
            raise ConnectionError("Dajin down")
        self.posts.append((path, body))
        return None


class FakeRemote:
    """Stand-in for attempt_delete: records calls, returns a fixed outcome."""
    def __init__(self, outcome=(DONE, None)):
        self.outcome = outcome
        self.calls = []

    def __call__(self, resource, daijin_id, *a, **k):
        self.calls.append((resource, daijin_id))
        return self.outcome


class SeqRemote:
    """attempt_delete que devuelve un resultado distinto por llamada (el último se
    repite). Sirve para el flujo GUARD -> recuperación -> re-intento."""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, resource, daijin_id, *a, **k):
        self.calls.append((resource, daijin_id))
        i = min(len(self.calls) - 1, len(self.outcomes) - 1)
        return self.outcomes[i]


def _wire(monkeypatch, mod, store, remote, st=None):
    st = st or FakeSmartTyre()
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "soft_delete", store.soft_delete)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "exists", store.exists, raising=False)
    monkeypatch.setattr(mod, "get_where", store.get_where, raising=False)
    monkeypatch.setattr(mod, "SmartTyreClient", lambda: st, raising=False)
    monkeypatch.setattr(mod, "attempt_delete", remote)
    return st


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


# --- cascada: al borrar la unidad se LIBERA el sensor de cada llanta montada en la
#     plataforma (con el vehicleId de la unidad), evitando que queden huérfanas ---
def test_vehicle_delete_cascade_unbinds_tire_sensor(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"}})
    store.mounted = [
        {"id": 5, "sensor_id": 42, "axle_index": 1, "wheel_index": 2},
    ]
    store.rows[5] = {"id": 5, "sensor_id": 42, "axle_index": 1, "wheel_index": 2}
    store.rows[42] = {"id": 42, "sensorCode": "A4C1388A0005"}
    remote = FakeRemote((DONE, None))
    st = _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    # primero desvinculó el sensor (con vehicleId + sensorCode), luego desmontó la llanta
    assert ("/smartyre/openapi/tyre/sensor/unbind", {
        "tyreCode": "5", "vehicleId": "33", "axleIndex": 1, "wheelIndex": 2,
        "sensorCode": "A4C1388A0005"}) in st.posts
    assert ("/smartyre/openapi/vehicle/tyre/unbind",
            {"vehicleId": "33", "tyreCode": "5"}) in st.posts
    # el sensor de la llanta quedó liberado en local (sobrevive en inventario)
    assert store.rows[5]["sensor_id"] is None


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


# --- cascada: la unidad desmonta sus llantas (plataforma + local) y luego se borra ---
# (antes esto bloqueaba con 409; el borrado ahora hace unbind automático)
def test_vehicle_delete_unmounts_mounted_tires(monkeypatch):
    store = FakeStore({
        1: {"id": 1, "is_deleted": 0, "tbox_id": None, "daijin_id": "33"},
        10: {"id": 10, "unit_id": 1, "is_mounted": 1},
    })
    store.mounted = [store.rows[10]]
    remote = FakeRemote((DONE, None))
    st = _wire(monkeypatch, vdel, store, remote)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    # unbind remoto de la llanta + desmontaje local, ANTES del borrado remoto
    assert ("/smartyre/openapi/vehicle/tyre/unbind",
            {"vehicleId": "33", "tyreCode": "10"}) in st.posts
    assert store.rows[10]["unit_id"] is None
    assert store.rows[10]["is_mounted"] == 0
    assert remote.calls == [("vehicle", "33")]
    assert store.rows[1]["is_deleted"] == 1


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


# (antes montada/con sensor bloqueaba con 409; el borrado ahora desvincula en cascada)
def test_tire_delete_unmounts_from_vehicle_first(monkeypatch):
    store = FakeStore({
        5: {"id": 5, "is_deleted": 0, "unit_id": 1, "sensor_id": None,
            "daijin_id": "77", "axle_index": 2, "wheel_index": 4},
        1: {"id": 1, "daijin_id": "33"},
    })
    remote = FakeRemote((DONE, None))
    st = _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    assert ("/smartyre/openapi/vehicle/tyre/unbind",
            {"vehicleId": "33", "tyreCode": "5"}) in st.posts
    assert store.rows[5]["unit_id"] is None
    assert remote.calls == [("tyre", "77")]


def test_tire_delete_stored_with_sensor_frees_it_even_if_dajin_rejects(monkeypatch):
    # Regla de negocio: una llanta puede almacenarse CON su sensor. Al borrarla,
    # el unbind remoto no tiene contexto de vehículo y Dajin lo rechaza — eso no
    # debe bloquear: el sensor se libera localmente y el borrado continúa.
    store = FakeStore({
        5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": 9,
            "daijin_id": "77", "axle_index": None, "wheel_index": None},
        9: {"id": 9, "sensorCode": "A4C13873C3E6"},
    })
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, tdel, store, remote, st=FakeSmartTyre(fail=True))
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    assert store.rows[5]["sensor_id"] is None      # liberado (sigue en inventario)
    assert store.rows[5]["is_deleted"] == 1
    assert remote.calls == [("tyre", "77")]


def test_tire_delete_mounted_sensor_unbind_failure_still_aborts(monkeypatch):
    # Montada, el rechazo del unbind del sensor SÍ es real: 502 y nada local se toca.
    store = FakeStore({
        5: {"id": 5, "is_deleted": 0, "unit_id": 1, "sensor_id": 9,
            "daijin_id": "77", "axle_index": 2, "wheel_index": 4},
        1: {"id": 1, "daijin_id": "33"},
        9: {"id": 9, "sensorCode": "A4C13873C3E6"},
    })
    remote = FakeRemote((DONE, None))
    _wire(monkeypatch, tdel, store, remote, st=FakeSmartTyre(fail=True))
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 502
    assert store.rows[5]["sensor_id"] == 9
    assert store.rows[5]["is_deleted"] == 0
    assert remote.calls == []


def test_tire_delete_frees_sensor_first(monkeypatch):
    store = FakeStore({
        5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": 9,
            "daijin_id": "77", "axle_index": None, "wheel_index": None},
        9: {"id": 9, "sensorCode": "A4C13873C3E6"},
    })
    remote = FakeRemote((DONE, None))
    st = _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    # el sensor se libera (queda en inventario) antes de borrar la llanta
    assert st.posts[0][0] == "/smartyre/openapi/tyre/sensor/unbind"
    assert st.posts[0][1]["sensorCode"] == "A4C13873C3E6"
    assert store.rows[5]["sensor_id"] is None
    assert remote.calls == [("tyre", "77")]


# ---------- llanta genérica de paquete: recuperación de la plataforma ----------
# Escenario real: la llanta ya está LIMPIA en local (desmontada, sin sensor), pero en
# la plataforma sigue montada con su sensor. El primer borrado remoto hace GUARD; tras
# reconstruir vehículo/sensor desde el paquete y limpiar la plataforma, el segundo
# borrado confirma y recién entonces se borra en local.
def _pkg_store():
    return FakeStore({
        # llanta genérica de paquete: local ya limpia, pero daijin_id vivo.
        5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": None,
            "daijin_id": "77", "folio": "PKG10-2",
            "axle_index": None, "wheel_index": None},
        # paquete que armó la llanta -> unidad + unit_catalog.
        10: {"id": 10, "unit_id": 20, "unit_catalog_id": 30},
        # unidad real: su daijin_id es el vehicleId de la plataforma.
        20: {"id": 20, "daijin_id": "33"},
        # unit_catalog: 1 eje con 2 llantas -> pos 2 = (axle 1, wheel 2).
        30: {"id": 30, "axles_count": 1, "tires_axle_1": 2},
    })


def test_pkg_tire_delete_recovers_platform_then_deletes(monkeypatch):
    store = _pkg_store()
    store.pkg_sensors = [
        {"id": 41, "package_id": 10, "mount_position": 1, "sensorCode": "OTHER"},
        {"id": 42, "package_id": 10, "mount_position": 2, "sensorCode": "A4C13873C3E6"},
    ]
    # 1er attempt_delete: GUARD (531); 2do (tras limpiar): DONE.
    remote = SeqRemote([(GUARD, "轮胎已绑定传感器"), (DONE, None)])
    st = _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    # la recuperación desvinculó el sensor con el vehicleId + sensorCode del paquete
    assert ("/smartyre/openapi/tyre/sensor/unbind", {
        "tyreCode": "5", "vehicleId": "33", "axleIndex": 1, "wheelIndex": 2,
        "sensorCode": "A4C13873C3E6"}) in st.posts
    # y desmontó la llanta en la plataforma
    assert ("/smartyre/openapi/vehicle/tyre/unbind",
            {"vehicleId": "33", "tyreCode": "5"}) in st.posts
    # se reintentó el borrado remoto y recién entonces se cerró en local
    assert remote.calls == [("tyre", "77"), ("tyre", "77")]
    assert store.rows[5]["is_deleted"] == 1
    assert store.rows[5]["daijin_id"] is None


def test_pkg_tire_delete_orphan_deletes_sensor_then_tyre(monkeypatch):
    # Unidad del paquete BORRADA (sin daijin_id): no hay vehicleId para soltar el
    # sensor "en frío", así que la limpieza no basta y el retry sigue GUARD. Fallback:
    # borrar el sensor en la plataforma libera la llanta; el sensor local SOBREVIVE
    # (se limpia su daijin_id) y la llanta se borra. Sin medio-estado.
    store = FakeStore({
        5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": None,
            "daijin_id": "77", "folio": "PKG10-2", "axle_index": None, "wheel_index": None},
        10: {"id": 10, "unit_id": 20, "unit_catalog_id": 30},
        20: {"id": 20, "daijin_id": None},          # unidad borrada: sin vehicleId
        30: {"id": 30, "axles_count": 1, "tires_axle_1": 2},
        42: {"id": 42, "daijin_id": "282720"},      # fila del sensor (para el update local)
    })
    store.pkg_sensors = [
        {"id": 42, "package_id": 10, "mount_position": 2, "sensorCode": "A4C1388A0005",
         "daijin_id": "282720"},
    ]
    # tyre GUARD, tyre GUARD (limpieza en frío no bastó), sensor DONE, tyre DONE.
    remote = SeqRemote([(GUARD, "531"), (GUARD, "531"), (DONE, None), (DONE, None)])
    _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    # se borró el sensor en la plataforma para liberar la llanta huérfana
    assert ("sensor", "282720") in remote.calls
    # el sensor local sobrevive, sin daijin_id (vuelve a sincronizar al reutilizarse)
    assert store.rows[42]["daijin_id"] is None
    # la llanta se cerró en local recién tras el borrado remoto confirmado
    assert store.rows[5]["is_deleted"] == 1


def test_pkg_tire_delete_still_guard_does_not_soft_delete(monkeypatch):
    store = _pkg_store()
    store.pkg_sensors = [
        {"id": 42, "package_id": 10, "mount_position": 2, "sensorCode": "A4C13873C3E6"},
    ]
    # incluso tras limpiar la plataforma, el borrado sigue en GUARD -> 409, sin medio-estado.
    remote = FakeRemote((GUARD, "轮胎已绑定传感器"))
    _wire(monkeypatch, tdel, store, remote)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 409
    assert store.rows[5]["is_deleted"] == 0        # NO se borró en local
    assert store.soft_deleted == []
    # solo el update no debe haber marcado is_deleted
    assert all(data.get("is_deleted") != 1 for _, data in store.updated)


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
