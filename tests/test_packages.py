import json
from collections import defaultdict

import pytest

from functions.packages import create as pcreate
from functions.packages import move as pmove
from functions.packages import assign as passign
from functions.packages import unassign as punassign
from functions.packages import list as plist
from functions.packages import edit as pedit


# --------------------------------------------------------------------------- #
#  Fakes compartidos (DB + store table-aware, como los otros tests del repo)   #
# --------------------------------------------------------------------------- #
class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Store:
    """Almacén en memoria por tabla: {table: {id: row}}."""

    def __init__(self):
        self.tables = defaultdict(dict)
        self.seq = defaultdict(int)

    def seed(self, table, row):
        self.tables[table][row["id"]] = dict(row)
        self.seq[table] = max(self.seq[table], row["id"])

    def insert(self, db, table, data):
        self.seq[table] += 1
        rid = self.seq[table]
        row = {"id": rid, **data}
        self.tables[table][rid] = row
        return dict(row)

    def update(self, db, table, rid, data):
        self.tables[table][rid].update(data)
        return dict(self.tables[table][rid])

    def get_by_id(self, db, table, rid):
        r = self.tables[table].get(rid)
        return dict(r) if r else None

    def get_many(self, db, table, columns="*", filters=None, limit=300):
        rows = sorted(self.tables[table].values(), key=lambda r: r["id"], reverse=True)
        if filters:
            rows = [r for r in rows if all(r.get(k) == v for k, v in filters.items())]
        return [dict(r) for r in rows[:limit]]

    def get_in(self, db, table, field, values, columns="*"):
        vals = set(values)
        return [dict(r) for r in self.tables[table].values() if r.get(field) in vals]

    def get_where(self, db, table, where, params=(), limit=200):
        rows = sorted(self.tables[table].values(), key=lambda r: r["id"])
        if "mount_position" in where:
            uid, pos = params[0], params[1]
            out = [dict(r) for r in rows
                   if r.get("unit_id") == uid and r.get("mount_position") == pos
                   and not r.get("is_deleted")]
        elif "package_id" in where:
            pid = params[0]
            out = [dict(r) for r in rows if r.get("package_id") == pid]
        elif "folio" in where:
            # unassign: "unit_id=%s AND [sensor_id IS NOT NULL AND] folio [NOT] LIKE %s ..."
            uid, pat = params[0], params[1]
            prefix = str(pat).rstrip("%")
            not_like = "NOT LIKE" in where
            needs_sensor = "sensor_id IS NOT NULL" in where
            out = []
            for r in rows:
                if r.get("unit_id") != uid or r.get("is_deleted"):
                    continue
                starts = str(r.get("folio") or "").startswith(prefix)
                if not_like and starts:
                    continue
                if not not_like and not starts:
                    continue
                if needs_sensor and not r.get("sensor_id"):
                    continue
                out.append(dict(r))
        else:
            out = [dict(r) for r in rows]
        return out[:limit]


def _resp(status, body):
    return {"statusCode": status, "body": json.dumps(body)}


def _body(resp):
    return json.loads(resp["body"])


# --------------------------------------------------------------------------- #
#  create.py                                                                   #
# --------------------------------------------------------------------------- #
def _wire_create(monkeypatch, store, db):
    monkeypatch.setattr(pcreate, "get_db", lambda: db)
    monkeypatch.setattr(pcreate, "get_by_id", store.get_by_id)
    monkeypatch.setattr(pcreate, "insert", store.insert)
    monkeypatch.setattr(pcreate, "update", store.update)
    monkeypatch.setattr(pcreate, "audit", lambda *a, **k: None)

    def fake_tbox(event, context):
        b = json.loads(event["body"])
        row = store.insert(db, "tboxes", {
            "tboxCode": b["tbox_code"], "company_id": b["company_id"],
            "daijin_id": 900, "status": "active", "package_id": None,
        })
        return _resp(200, row)

    def fake_sensor(event, context):
        b = json.loads(event["body"])
        row = store.insert(db, "sensors", {
            "sensorCode": b["sensor_code"], "company_id": b["company_id"],
            "daijin_id": 800, "status": "active", "package_id": None,
        })
        return _resp(200, row)

    monkeypatch.setattr(pcreate, "tbox_create_handler", fake_tbox)
    monkeypatch.setattr(pcreate, "sensor_create_handler", fake_sensor)


def _catalog_2axles():
    # axles_count=2, tires_axle_1=2, tires_axle_2=4  -> N = 6 llantas/sensores
    return {"id": 209, "name": "tractor", "type": "motive",
            "axles_count": 2, "tires_axle_1": 2, "tires_axle_2": 4}


def _create_event(sensors, company_id=None, catalog=209, tbox="A4C13873C3E6"):
    payload = {"unit_catalog_id": catalog, "tboxCode": tbox, "sensorCodes": sensors}
    if company_id is not None:
        payload["company_id"] = company_id
    return {"body": json.dumps(payload)}


def test_create_rejects_non_admin_company(monkeypatch):
    store, db = Store(), FakeDB()
    _wire_create(monkeypatch, store, db)
    resp = pcreate.handler(_create_event([f"{i:012X}" for i in range(6)], company_id=99), None)
    assert resp["statusCode"] == 422
    assert "admin" in json.dumps(_body(resp)).lower()


def test_create_validates_sensor_count(monkeypatch):
    store, db = Store(), FakeDB()
    store.seed("unit_catalog", _catalog_2axles())
    _wire_create(monkeypatch, store, db)
    # el catálogo pide 6 sensores; mandamos 3
    resp = pcreate.handler(_create_event([f"{i:012X}" for i in range(3)]), None)
    assert resp["statusCode"] == 422
    assert "6 sensores" in json.dumps(_body(resp))


def test_create_happy_builds_prepared_package(monkeypatch):
    store, db = Store(), FakeDB()
    store.seed("unit_catalog", _catalog_2axles())
    _wire_create(monkeypatch, store, db)
    codes = [f"{i:012X}" for i in range(6)]
    resp = pcreate.handler(_create_event(codes), None)  # company_id ausente -> default admin (2)
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["status"] == "prepared"
    assert data["company_id"] == 2
    # el tbox y los 6 sensores quedaron sellados con el package_id
    assert data["tbox"]["package_id"] == data["id"]
    assert len(data["sensors"]) == 6
    assert all(s["package_id"] == data["id"] for s in data["sensors"])
    # cada sensor guarda su mount_position 1-based en el orden de captura (1..6)
    assert [s["mount_position"] for s in data["sensors"]] == [1, 2, 3, 4, 5, 6]


def test_create_lote_without_tbox(monkeypatch):
    # Un lote = paquete SIN tbox (solo sensores), para tipos no motrices (remolques).
    store, db = Store(), FakeDB()
    store.seed("unit_catalog", _catalog_2axles())
    calls = defaultdict(int)
    _wire_create(monkeypatch, store, db)

    # el fake_tbox NO debe llamarse: contamos las llamadas envolviéndolo.
    def guard_tbox(event, context):
        calls["tbox"] += 1
        raise AssertionError("no se debe crear tbox en un lote")

    monkeypatch.setattr(pcreate, "tbox_create_handler", guard_tbox)

    codes = [f"{i:012X}" for i in range(6)]
    # body SIN tboxCode -> lote
    resp = pcreate.handler({"body": json.dumps(
        {"unit_catalog_id": 209, "sensorCodes": codes})}, None)
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["status"] == "prepared"
    # no se creó tbox: ni se llamó al handler ni hay filas en la tabla
    assert calls["tbox"] == 0
    assert store.tables["tboxes"] == {}
    # la respuesta no trae tbox, pero sí los N sensores sellados con mount_position
    assert data["tbox"] is None
    assert len(data["sensors"]) == 6
    assert all(s["package_id"] == data["id"] for s in data["sensors"])
    assert [s["mount_position"] for s in data["sensors"]] == [1, 2, 3, 4, 5, 6]


# --------------------------------------------------------------------------- #
#  move.py                                                                     #
# --------------------------------------------------------------------------- #
def _wire_move(monkeypatch, store, db):
    monkeypatch.setattr(pmove, "get_db", lambda: db)
    monkeypatch.setattr(pmove, "get_by_id", store.get_by_id)
    monkeypatch.setattr(pmove, "get_where", store.get_where)
    monkeypatch.setattr(pmove, "update", store.update)
    monkeypatch.setattr(pmove, "audit", lambda *a, **k: None)


def _seed_package(store, status="prepared", company_id=2):
    store.seed("packages", {"id": 1, "name": "kit", "unit_catalog_id": 209,
                            "company_id": company_id, "unit_id": None, "status": status})
    store.seed("tboxes", {"id": 50, "tboxCode": "AA", "company_id": company_id, "package_id": 1})
    store.seed("sensors", {"id": 20, "sensorCode": "BB", "company_id": company_id, "package_id": 1})
    store.seed("sensors", {"id": 21, "sensorCode": "CC", "company_id": company_id, "package_id": 1})


def _move_event(pid, company_id):
    return {"pathParameters": {"id": str(pid)}, "body": json.dumps({"company_id": company_id})}


def test_move_happy_cascades_company(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_package(store, status="prepared", company_id=2)
    _wire_move(monkeypatch, store, db)
    resp = pmove.handler(_move_event(1, 5), None)
    assert resp["statusCode"] == 200
    assert store.tables["packages"][1]["company_id"] == 5
    assert store.tables["tboxes"][50]["company_id"] == 5
    assert store.tables["sensors"][20]["company_id"] == 5
    assert store.tables["sensors"][21]["company_id"] == 5


def test_move_only_when_prepared(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_package(store, status="assigned", company_id=5)
    _wire_move(monkeypatch, store, db)
    resp = pmove.handler(_move_event(1, 7), None)
    assert resp["statusCode"] == 409
    # no se tocó la compañía
    assert store.tables["packages"][1]["company_id"] == 5


# --------------------------------------------------------------------------- #
#  assign.py  (reutiliza-o-crea llanta)                                        #
# --------------------------------------------------------------------------- #
def _wire_assign(monkeypatch, store, db, calls):
    monkeypatch.setenv("GENERIC_TIRES_CATALOG_ID", "777")
    monkeypatch.setattr(passign, "get_db", lambda: db)
    monkeypatch.setattr(passign, "get_by_id", store.get_by_id)
    monkeypatch.setattr(passign, "get_where", store.get_where)
    monkeypatch.setattr(passign, "update", store.update)
    monkeypatch.setattr(passign, "audit", lambda *a, **k: None)

    def fake_tire_create(event, context):
        b = json.loads(event["body"])
        calls["tire_create"] += 1
        row = store.insert(db, "tires", {
            "prefix": b["prefix"], "folio": b["folio"], "company_id": b["company_id"],
            "tires_catalog_id": b["tires_catalog_id"], "daijin_id": 414997,
            "is_mounted": 0, "sensor_id": None, "unit_id": None,
            "mount_position": None, "status": "new",
        })
        return _resp(200, row)

    def fake_bind_tire(event, context):
        calls["bind_tire"] += 1
        return _resp(200, {})

    def fake_bind_sensor(event, context):
        calls["bind_sensor"] += 1
        return _resp(200, {})

    def fake_bind_tbox(event, context):
        calls["bind_tbox"] += 1
        return _resp(200, {})

    monkeypatch.setattr(passign, "tire_create_handler", fake_tire_create)
    monkeypatch.setattr(passign, "bind_tire_handler", fake_bind_tire)
    monkeypatch.setattr(passign, "bind_sensor_handler", fake_bind_sensor)
    monkeypatch.setattr(passign, "bind_tbox_handler", fake_bind_tbox)


def _seed_for_assign(store):
    # layout: axles_count=1, tires_axle_1=2 -> 2 posiciones (mount_position 1 y 2)
    store.seed("unit_catalog", {"id": 209, "name": "van", "type": "motive",
                                "axles_count": 1, "tires_axle_1": 2})
    store.seed("packages", {"id": 1, "name": "kit", "unit_catalog_id": 209,
                            "company_id": 5, "unit_id": None, "status": "prepared"})
    store.seed("units", {"id": 1, "unit_catalog_id": 209, "company_id": 5, "daijin_id": 33369})
    store.seed("tboxes", {"id": 50, "tboxCode": "AA", "company_id": 5, "package_id": 1})
    store.seed("sensors", {"id": 20, "sensorCode": "BB", "company_id": 5, "package_id": 1})
    store.seed("sensors", {"id": 21, "sensorCode": "CC", "company_id": 5, "package_id": 1})


def _assign_event(pid, unit_id):
    return {"pathParameters": {"id": str(pid)}, "body": json.dumps({"unit_id": unit_id})}


def test_assign_reuses_existing_tire_and_creates_missing(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_for_assign(store)
    # ya hay una llanta montada en la posición 1 -> debe REUTILIZARSE
    store.seed("tires", {"id": 10, "unit_id": 1, "mount_position": 1, "is_mounted": 1,
                         "sensor_id": None, "is_deleted": 0, "daijin_id": 414997})
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 200
    # solo se creó UNA llanta (posición 2); la 1 se reutilizó
    assert calls["tire_create"] == 1
    # la reutilizada ya estaba montada -> solo se bindea la nueva
    assert calls["bind_tire"] == 1
    # ambas posiciones reciben sensor
    assert calls["bind_sensor"] == 2
    assert calls["bind_tbox"] == 1
    # el paquete queda asignado a la unidad
    assert store.tables["packages"][1]["status"] == "assigned"
    assert store.tables["packages"][1]["unit_id"] == 1


def test_assign_creates_all_tires_when_unit_empty(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_for_assign(store)  # sin llantas previas
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 200
    assert calls["tire_create"] == 2  # se crean las 2 posiciones
    assert calls["bind_tire"] == 2
    assert calls["bind_sensor"] == 2
    assert calls["bind_tbox"] == 1


def test_assign_rejects_catalog_mismatch(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_for_assign(store)
    store.tables["units"][1]["unit_catalog_id"] = 999  # no coincide con el paquete
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)
    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 422
    assert calls["tire_create"] == 0


def test_assign_resolves_generic_by_convention_when_no_env(monkeypatch):
    # Sin GENERIC_TIRES_CATALOG_ID, la genérica se resuelve por la fila centinela
    # (Desconocida/DESCONOCIDA/ALL) del catálogo, igual que el FE.
    store, db = Store(), FakeDB()
    _seed_for_assign(store)  # unidad vacía -> se crean las 2 llantas genéricas
    store.seed("tires_catalog", {"id": 555, "brand": "Desconocida",
                                 "model": "DESCONOCIDA", "size": "DESCONOCIDA", "position": "ALL"})
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)
    monkeypatch.delenv("GENERIC_TIRES_CATALOG_ID", raising=False)  # forzar la convención

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 200
    assert calls["tire_create"] == 2
    created = list(store.tables["tires"].values())
    assert created and all(r["tires_catalog_id"] == 555 for r in created)


def test_assign_500_when_no_generic_and_no_env(monkeypatch):
    # Ni fila centinela ni env -> 500 claro (no se crea nada).
    store, db = Store(), FakeDB()
    _seed_for_assign(store)  # unidad vacía, sin tires_catalog centinela
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)
    monkeypatch.delenv("GENERIC_TIRES_CATALOG_ID", raising=False)

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 500
    assert calls["tire_create"] == 0


def test_assign_maps_sensor_by_mount_position_not_id(monkeypatch):
    # sensores con mount_position INVERTIDO respecto al id: id 20 -> pos 2, id 21 -> pos 1.
    # El assign debe mapear sensor->posición por mount_position, no por orden de id.
    store, db = Store(), FakeDB()
    _seed_for_assign(store)  # unidad vacía, 2 posiciones
    store.tables["sensors"][20]["mount_position"] = 2
    store.tables["sensors"][21]["mount_position"] = 1
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)

    bound = []

    def capture_bind_sensor(event, context):
        bound.append(json.loads(event["body"])["sensor_id"])
        return _resp(200, {})

    monkeypatch.setattr(passign, "bind_sensor_handler", capture_bind_sensor)

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 200
    # posición 1 -> sensor con mount_position 1 (id 21); posición 2 -> mount_position 2 (id 20)
    assert bound == [21, 20]


def test_assign_lote_without_tbox(monkeypatch):
    # Un lote (paquete SIN tbox) se asigna igual: monta llantas + sensores, pero
    # NUNCA ata tbox. El paquete queda 'assigned'.
    store, db = Store(), FakeDB()
    _seed_for_assign(store)
    # quitar el tbox del seed -> paquete sin tbox (lote)
    del store.tables["tboxes"][50]
    store.tables["sensors"][20]["mount_position"] = 1
    store.tables["sensors"][21]["mount_position"] = 2
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 200
    assert calls["tire_create"] == 2
    assert calls["bind_tire"] == 2
    assert calls["bind_sensor"] == 2
    # el tbox NUNCA se ata en un lote
    assert calls["bind_tbox"] == 0
    assert store.tables["packages"][1]["status"] == "assigned"
    assert store.tables["packages"][1]["unit_id"] == 1


# --------------------------------------------------------------------------- #
#  unassign.py  (deshace el assign)                                            #
# --------------------------------------------------------------------------- #
def _seed_assigned(store):
    store.seed("unit_catalog", {"id": 209, "axles_count": 1, "tires_axle_1": 2})
    store.seed("packages", {"id": 1, "name": "kit", "unit_catalog_id": 209,
                            "company_id": 5, "unit_id": 1, "status": "assigned"})
    store.seed("units", {"id": 1, "unit_catalog_id": 209, "company_id": 5,
                         "daijin_id": 33369, "tbox_id": 50})
    store.seed("tboxes", {"id": 50, "tboxCode": "AA", "company_id": 5, "package_id": 1})
    store.seed("sensors", {"id": 20, "sensorCode": "BB", "company_id": 5,
                           "package_id": 1, "mount_position": 1})
    store.seed("sensors", {"id": 21, "sensorCode": "CC", "company_id": 5,
                           "package_id": 1, "mount_position": 2})


def _wire_unassign(monkeypatch, store, db, calls):
    monkeypatch.setattr(punassign, "get_db", lambda: db)
    monkeypatch.setattr(punassign, "get_by_id", store.get_by_id)
    monkeypatch.setattr(punassign, "get_where", store.get_where)
    monkeypatch.setattr(punassign, "update", store.update)
    monkeypatch.setattr(punassign, "audit", lambda *a, **k: None)

    def fake_unbind_tbox(event, context):
        calls["unbind_tbox"] += 1
        return _resp(200, {})

    def fake_unbind_sensor(event, context):
        calls["unbind_sensor"] += 1
        return _resp(200, {})

    def fake_tire_delete(event, context):
        calls["tire_delete"] += 1
        return _resp(200, {})

    monkeypatch.setattr(punassign, "unbind_tbox_handler", fake_unbind_tbox)
    monkeypatch.setattr(punassign, "unbind_sensor_handler", fake_unbind_sensor)
    monkeypatch.setattr(punassign, "tire_delete_handler", fake_tire_delete)


def _unassign_event(pid):
    return {"pathParameters": {"id": str(pid)}}


def test_unassign_deletes_generic_tires_and_unbinds_tbox(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_assigned(store)
    # 2 llantas que creó el paquete (folio PKG1-...), cada una con un sensor del kit
    store.seed("tires", {"id": 10, "unit_id": 1, "folio": "PKG1-1", "mount_position": 1,
                         "is_mounted": 1, "sensor_id": 20, "is_deleted": 0})
    store.seed("tires", {"id": 11, "unit_id": 1, "folio": "PKG1-2", "mount_position": 2,
                         "is_mounted": 1, "sensor_id": 21, "is_deleted": 0})
    calls = defaultdict(int)
    _wire_unassign(monkeypatch, store, db, calls)

    resp = punassign.handler(_unassign_event(1), None)
    assert resp["statusCode"] == 200
    assert calls["tire_delete"] == 2       # borra las 2 genéricas que creó
    assert calls["unbind_sensor"] == 0     # no hay reales reutilizadas
    assert calls["unbind_tbox"] == 1
    assert store.tables["packages"][1]["status"] == "prepared"
    assert store.tables["packages"][1]["unit_id"] is None


def test_unassign_keeps_reused_real_tire(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_assigned(store)
    # una llanta REAL de la unidad (folio real), con un sensor del kit -> solo unbind sensor
    store.seed("tires", {"id": 12, "unit_id": 1, "folio": "R-500", "mount_position": 1,
                         "is_mounted": 1, "sensor_id": 20, "is_deleted": 0})
    calls = defaultdict(int)
    _wire_unassign(monkeypatch, store, db, calls)

    resp = punassign.handler(_unassign_event(1), None)
    assert resp["statusCode"] == 200
    assert calls["tire_delete"] == 0       # la real NO se borra
    assert calls["unbind_sensor"] == 1     # solo se le quita el sensor del kit
    assert calls["unbind_tbox"] == 1
    assert store.tables["packages"][1]["status"] == "prepared"


def test_unassign_rejects_when_not_assigned(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_assigned(store)
    store.tables["packages"][1]["status"] = "prepared"
    calls = defaultdict(int)
    _wire_unassign(monkeypatch, store, db, calls)
    resp = punassign.handler(_unassign_event(1), None)
    assert resp["statusCode"] == 409
    assert calls["tire_delete"] == 0


def test_assign_202_with_progress_and_audit_on_pending(monkeypatch):
    # Si un sub-alta de llanta queda pending en la plataforma, el assign devuelve 202
    # con el progreso (done/total) y deja un audit result='pending'.
    store, db = Store(), FakeDB()
    _seed_for_assign(store)  # 2 posiciones, unidad vacía
    calls = defaultdict(int)
    _wire_assign(monkeypatch, store, db, calls)

    def pending_tire_create(event, context):
        calls["tire_create"] += 1
        return _resp(202, {"data": {"reason": "plataforma pendiente"}})

    audits = []
    monkeypatch.setattr(passign, "tire_create_handler", pending_tire_create)
    monkeypatch.setattr(passign, "audit", lambda *a, **k: audits.append(k))

    resp = passign.handler(_assign_event(1, 1), None)
    assert resp["statusCode"] == 202
    d = _body(resp)["data"]
    assert d["done"] == 0 and d["total"] == 2  # se cortó en la 1ª posición
    assert any(a.get("result") == "pending" for a in audits)


# --------------------------------------------------------------------------- #
#  list.py  (enriquecido con sensor_count + tboxCode)                          #
# --------------------------------------------------------------------------- #
def _wire_list(monkeypatch, store, db):
    monkeypatch.setattr(plist, "get_db", lambda: db)
    monkeypatch.setattr(plist, "get_many", store.get_many)
    monkeypatch.setattr(plist, "get_in", store.get_in)


def test_list_enriches_with_sensor_count_and_tboxcode(monkeypatch):
    store, db = Store(), FakeDB()
    # un paquete con su tbox + 2 sensores sellados, y otro paquete vacío
    store.seed("packages", {"id": 1, "name": "kit", "unit_catalog_id": 209,
                            "company_id": 2, "unit_id": None, "status": "prepared"})
    store.seed("packages", {"id": 2, "name": "vacio", "unit_catalog_id": 209,
                            "company_id": 2, "unit_id": None, "status": "prepared"})
    store.seed("tboxes", {"id": 50, "tboxCode": "AA11BB22CC33", "company_id": 2, "package_id": 1})
    store.seed("sensors", {"id": 20, "sensorCode": "BB", "company_id": 2, "package_id": 1})
    store.seed("sensors", {"id": 21, "sensorCode": "CC", "company_id": 2, "package_id": 1})
    _wire_list(monkeypatch, store, db)

    resp = plist.handler({}, None)
    assert resp["statusCode"] == 200
    rows = {r["id"]: r for r in _body(resp)}
    assert rows[1]["sensor_count"] == 2
    assert rows[1]["tboxCode"] == "AA11BB22CC33"
    # el paquete sin miembros reporta 0 sensores y tboxCode None
    assert rows[2]["sensor_count"] == 0
    assert rows[2]["tboxCode"] is None


# --------------------------------------------------------------------------- #
#  edit.py  (PUT /packages/{id})                                              #
# --------------------------------------------------------------------------- #
def _wire_edit(monkeypatch, store, db):
    monkeypatch.setattr(pedit, "get_db", lambda: db)
    monkeypatch.setattr(pedit, "get_by_id", store.get_by_id)
    monkeypatch.setattr(pedit, "get_where", store.get_where)
    monkeypatch.setattr(pedit, "update", store.update)
    monkeypatch.setattr(pedit, "audit", lambda *a, **k: None)

    def fake_tbox(event, context):
        b = json.loads(event["body"])
        # idempotente: reutiliza un tbox vivo con el mismo código, o crea uno nuevo.
        for r in store.tables["tboxes"].values():
            if r.get("tboxCode") == b["tbox_code"]:
                return _resp(200, dict(r))
        row = store.insert(db, "tboxes", {
            "tboxCode": b["tbox_code"], "company_id": b["company_id"],
            "daijin_id": 900, "status": "active", "package_id": None,
        })
        return _resp(200, row)

    def fake_sensor(event, context):
        b = json.loads(event["body"])
        for r in store.tables["sensors"].values():
            if r.get("sensorCode") == b["sensor_code"]:
                return _resp(200, dict(r))
        row = store.insert(db, "sensors", {
            "sensorCode": b["sensor_code"], "company_id": b["company_id"],
            "daijin_id": 800, "status": "active", "package_id": None,
        })
        return _resp(200, row)

    monkeypatch.setattr(pedit, "tbox_create_handler", fake_tbox)
    monkeypatch.setattr(pedit, "sensor_create_handler", fake_sensor)


def _seed_editable(store, status="prepared"):
    # layout: axles_count=1, tires_axle_1=2 -> N = 2 sensores
    store.seed("unit_catalog", {"id": 209, "name": "van", "type": "motive",
                                "axles_count": 1, "tires_axle_1": 2})
    store.seed("packages", {"id": 1, "name": "kit", "unit_catalog_id": 209,
                            "company_id": 2, "unit_id": None, "status": status})
    store.seed("tboxes", {"id": 50, "tboxCode": "AA11BB22CC33", "company_id": 2, "package_id": 1})
    store.seed("sensors", {"id": 20, "sensorCode": "AAAAAAAAAAAA", "company_id": 2,
                           "package_id": 1, "mount_position": 1})
    store.seed("sensors", {"id": 21, "sensorCode": "BBBBBBBBBBBB", "company_id": 2,
                           "package_id": 1, "mount_position": 2})


def _edit_event(pid, **body):
    return {"pathParameters": {"id": str(pid)}, "body": json.dumps(body)}


def test_edit_rejects_non_prepared(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_editable(store, status="assigned")
    _wire_edit(monkeypatch, store, db)
    resp = pedit.handler(_edit_event(1, name="nuevo"), None)
    assert resp["statusCode"] == 409
    assert "status=assigned" in json.dumps(_body(resp))
    # nada cambió
    assert store.tables["packages"][1]["name"] == "kit"


def test_edit_happy_swaps_sensor_and_reseals_tbox(monkeypatch):
    store, db = Store(), FakeDB()
    _seed_editable(store)
    _wire_edit(monkeypatch, store, db)

    # quitamos BBBB..., dejamos AAAA... y agregamos CCCC...; cambiamos el tbox.
    resp = pedit.handler(_edit_event(
        1, name="kit v2", tboxCode="DD44EE55FF66",
        sensorCodes=["AAAAAAAAAAAA", "CCCCCCCCCCCC"]), None)
    assert resp["statusCode"] == 200

    # nombre actualizado
    assert store.tables["packages"][1]["name"] == "kit v2"

    # el sensor removido (BBBB, id 21) queda desellado
    assert store.tables["sensors"][21]["package_id"] is None
    assert store.tables["sensors"][21]["mount_position"] is None

    # el set nuevo queda sellado con su mount_position 1-based (orden del set)
    sealed = {r["sensorCode"]: r for r in store.tables["sensors"].values()
              if r.get("package_id") == 1}
    assert set(sealed) == {"AAAAAAAAAAAA", "CCCCCCCCCCCC"}
    assert sealed["AAAAAAAAAAAA"]["mount_position"] == 1
    assert sealed["CCCCCCCCCCCC"]["mount_position"] == 2

    # el tbox se reselló: el viejo se desella, el nuevo cuelga del paquete
    assert store.tables["tboxes"][50]["package_id"] is None
    new_tbox = [r for r in store.tables["tboxes"].values()
                if r.get("tboxCode") == "DD44EE55FF66"][0]
    assert new_tbox["package_id"] == 1

    # la respuesta trae la forma de get.py (tbox + sensors actuales)
    data = _body(resp)
    assert data["tbox"]["tboxCode"] == "DD44EE55FF66"
    assert len(data["sensors"]) == 2
