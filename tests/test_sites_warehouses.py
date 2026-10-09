"""Sedes y almacenes por compañía.

Corre el SQL real de los handlers sobre SQLite en memoria (no un doble que
devuelve lo que el test espera): así se prueba el UPDATE ... IN, los conteos y
que el UNIQUE de nombres responda 409.

Lo que se protege, en orden:
  1. que ningún activo quede en la sede o el almacén de OTRA compañía
  2. que la admin (2) opere todo y las demás solo lo suyo
  3. que no se borre una sede o un almacén con cosas dentro
"""
import json
import sqlite3

import pymysql
import pytest

from functions.sites import assign as s_assign
from functions.sites import create as s_create
from functions.sites import delete as s_delete
from functions.sites import list as s_list
from functions.sites import update as s_update
from functions.warehouses import assign as w_assign
from functions.warehouses import create as w_create
from functions.warehouses import delete as w_delete
from functions.warehouses import update as w_update
from functions.lists import list_assets
from functions.packages import move as p_move
from functions.sensors import assign as sen_assign
from functions.tboxes import assign as tb_assign

ESQUEMA = """
CREATE TABLE companies (id INTEGER PRIMARY KEY, company_name TEXT);
CREATE TABLE sites (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INT NOT NULL,
  name TEXT NOT NULL, created_at INT, updated_at INT, UNIQUE (company_id, name));
CREATE TABLE warehouses (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INT NOT NULL,
  name TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'general', yard TEXT DEFAULT '',
  site_id INT NOT NULL REFERENCES sites (id), created_at INT, updated_at INT,
  UNIQUE (company_id, name, yard));
CREATE TABLE warehouse_tires (id INTEGER PRIMARY KEY, warehouse_id INT NOT NULL
  REFERENCES warehouses (id), tire_id INT);
CREATE TABLE units (id INTEGER PRIMARY KEY, company_id INT, site_id INT, tbox_id INT,
  is_deleted INT DEFAULT 0, updated_at INT);
CREATE TABLE tires (id INTEGER PRIMARY KEY, company_id INT, warehouse_id INT,
  sensor_id INT, is_deleted INT DEFAULT 0, updated_at INT);
CREATE TABLE sensors (id INTEGER PRIMARY KEY, company_id INT, warehouse_id INT,
  package_id INT, is_deleted INT DEFAULT 0, updated_at INT);
CREATE TABLE tboxes (id INTEGER PRIMARY KEY, company_id INT, warehouse_id INT,
  package_id INT, is_deleted INT DEFAULT 0, updated_at INT);
CREATE TABLE packages (id INTEGER PRIMARY KEY, company_id INT, name TEXT, status TEXT,
  updated_at INT);
CREATE TABLE asset_audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT,
  actor TEXT, action TEXT, asset_type TEXT, asset_id INT, natural_key TEXT,
  company_id INT, daijin_id TEXT, result TEXT, payload TEXT, changes TEXT, error TEXT,
  created_at INT);
"""


class _Cursor:
    def __init__(self, conn, como_dict):
        self._cur = conn.cursor()
        self._dict = como_dict

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._cur.close()

    def execute(self, sql, params=()):
        try:
            self._cur.execute(sql.replace("%s", "?"), list(params))
        except sqlite3.IntegrityError as e:
            # Igual que MySQL: 1062 para UNIQUE, 1451 para una FK que lo impide.
            code = 1062 if "UNIQUE" in str(e) else 1451
            raise pymysql.err.IntegrityError(code, str(e))

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def _fila(self, r):
        if r is None or not self._dict:
            return r
        return {d[0]: v for d, v in zip(self._cur.description, r)}

    def fetchone(self):
        return self._fila(self._cur.fetchone())

    def fetchall(self):
        return [self._fila(r) for r in self._cur.fetchall()]


class SQLiteDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(ESQUEMA)

    def cursor(self, clase=None):
        return _Cursor(self.conn, clase is pymysql.cursors.DictCursor)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def fila(self, tabla, rid):
        with self.cursor(pymysql.cursors.DictCursor) as c:
            c.execute(f"SELECT * FROM {tabla} WHERE id = %s", [rid])
            return c.fetchone()

    def sembrar(self, tabla, **campos):
        cols = ", ".join(campos)
        marcas = ", ".join(["?"] * len(campos))
        cur = self.conn.execute(f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})",
                                list(campos.values()))
        self.conn.commit()
        return cur.lastrowid


MODULOS = (s_assign, s_create, s_delete, s_list, s_update,
           w_assign, w_create, w_delete, w_update, list_assets,
           p_move, sen_assign, tb_assign)


@pytest.fixture
def db(monkeypatch):
    base = SQLiteDB()
    for m in MODULOS:
        monkeypatch.setattr(m, "get_db", lambda: base)
    monkeypatch.setattr(p_move, "audit", lambda *a, **k: None)
    for cid, nombre in ((2, "Quinta"), (8, "Cliente A"), (9, "Cliente B")):
        base.sembrar("companies", id=cid, company_name=nombre)
    return base


def _ev(body=None, id=None, company_id=None):
    ev = {"body": json.dumps(body) if body is not None else None, "headers": {}}
    if id is not None:
        ev["pathParameters"] = {"id": str(id)}
    if company_id is not None:
        ev["queryStringParameters"] = {"company_id": str(company_id)}
    return ev


def _ok(resp, status=200):
    assert resp["statusCode"] == status, resp["body"]
    return json.loads(resp["body"])


def _sede(db, company_id, nombre):
    return _ok(s_create.handler(_ev({"company_id": company_id, "name": nombre}), None))["id"]


def _almacen(db, company_id, nombre, site_id=None):
    # Sin sede explícita le crea una propia: un almacén siempre vive en una sede.
    if site_id is None:
        site_id = _sede(db, company_id, f"Base {nombre}")
    body = {"company_id": company_id, "name": nombre, "site_id": site_id}
    return _ok(w_create.handler(_ev(body), None))["id"]


# ─── Sedes ────────────────────────────────────────────────────────────────────

def test_crear_sede_limpia_el_nombre_y_deja_bitacora(db):
    rec = _ok(s_create.handler(_ev({"company_id": 8, "name": "  San   Luis  "}), None))
    assert rec["name"] == "San Luis" and rec["company_id"] == 8
    with db.cursor() as c:
        c.execute("SELECT asset_type, action FROM asset_audit_log")
        assert c.fetchall() == [("site", "create")]


def test_la_admin_crea_sedes_propias_y_de_otras_companias(db):
    _sede(db, 2, "Querétaro")
    _sede(db, 8, "Querétaro")  # mismo nombre en otra compañía: válido
    assert len(_ok(s_list.handler(_ev(company_id=2), None))["sites"]) == 2


def test_sede_repetida_en_la_misma_compania_es_409(db):
    _sede(db, 8, "SLP")
    resp = s_create.handler(_ev({"company_id": 8, "name": "SLP"}), None)
    assert resp["statusCode"] == 409


def test_sede_de_compania_inexistente_es_422(db):
    assert s_create.handler(_ev({"company_id": 77, "name": "X"}), None)["statusCode"] == 422


def test_nombre_vacio_es_422(db):
    assert s_create.handler(_ev({"company_id": 8, "name": "   "}), None)["statusCode"] == 422


def test_cliente_solo_ve_sus_sedes(db):
    _sede(db, 8, "SLP")
    _sede(db, 9, "MTY")
    sedes = _ok(s_list.handler(_ev(company_id=9), None))["sites"]
    assert [s["name"] for s in sedes] == ["MTY"]


def test_renombrar_sede_arrastra_el_yard_de_sus_almacenes(db):
    sid = _sede(db, 8, "SLP")
    wid = _almacen(db, 8, "Gallitos", sid)
    _ok(s_update.handler(_ev({"name": "San Luis"}, id=sid), None))
    assert db.fila("warehouses", wid)["yard"] == "San Luis"


def test_un_cliente_no_toca_la_sede_de_otro(db):
    sid = _sede(db, 8, "SLP")
    resp = s_update.handler(_ev({"name": "Mía"}, id=sid, company_id=9), None)
    assert resp["statusCode"] == 404
    assert s_delete.handler(_ev(id=sid, company_id=9), None)["statusCode"] == 404
    # La admin sí.
    _ok(s_update.handler(_ev({"name": "Mía"}, id=sid, company_id=2), None))


def test_no_se_borra_una_sede_con_unidades_o_almacenes(db):
    sid = _sede(db, 8, "SLP")
    db.sembrar("units", id=1, company_id=8, site_id=sid)
    assert s_delete.handler(_ev(id=sid), None)["statusCode"] == 409

    _ok(s_assign.handler(_ev({"site_id": None, "unit_ids": [1]}), None))
    wid = _almacen(db, 8, "Gallitos", sid)
    assert s_delete.handler(_ev(id=sid), None)["statusCode"] == 409

    _ok(w_delete.handler(_ev(id=wid), None))
    _ok(s_delete.handler(_ev(id=sid), None))
    assert db.fila("sites", sid) is None


def test_borrar_sede_suelta_a_las_unidades_borradas_que_la_apuntaban(db):
    sid = _sede(db, 8, "SLP")
    db.sembrar("units", id=1, company_id=8, site_id=sid, is_deleted=1)
    _ok(s_delete.handler(_ev(id=sid), None))
    assert db.fila("units", 1)["site_id"] is None


# ─── Unidades a sedes ────────────────────────────────────────────────────────

def test_asignar_unidades_a_una_sede(db):
    sid = _sede(db, 8, "SLP")
    for i in (1, 2):
        db.sembrar("units", id=i, company_id=8)
    out = _ok(s_assign.handler(_ev({"site_id": sid, "unit_ids": [2, 1, 2]}), None))
    assert out["unit_ids"] == [1, 2]
    assert db.fila("units", 1)["site_id"] == sid == db.fila("units", 2)["site_id"]
    assert _ok(s_list.handler(_ev(company_id=8), None))["sites"][0]["unit_count"] == 2


def test_una_unidad_de_otra_compania_no_entra_y_no_se_mueve_ninguna(db):
    sid = _sede(db, 8, "SLP")
    db.sembrar("units", id=1, company_id=8)
    db.sembrar("units", id=2, company_id=9)
    resp = s_assign.handler(_ev({"site_id": sid, "unit_ids": [1, 2]}), None)
    assert resp["statusCode"] == 422
    assert json.loads(resp["body"])["error"]["unit_ids"] == [2]
    assert db.fila("units", 1)["site_id"] is None  # todo o nada


def test_unidad_borrada_o_inexistente_es_422(db):
    sid = _sede(db, 8, "SLP")
    db.sembrar("units", id=1, company_id=8, is_deleted=1)
    resp = s_assign.handler(_ev({"site_id": sid, "unit_ids": [1, 99]}), None)
    assert resp["statusCode"] == 422
    assert json.loads(resp["body"])["error"]["unit_ids"] == [1, 99]


def test_cliente_no_asigna_a_la_sede_de_otro(db):
    sid = _sede(db, 8, "SLP")
    db.sembrar("units", id=1, company_id=8)
    resp = s_assign.handler(_ev({"site_id": sid, "unit_ids": [1]}, company_id=9), None)
    assert resp["statusCode"] == 404


# ─── Almacenes ───────────────────────────────────────────────────────────────

def test_almacen_en_sede_de_otra_compania_es_422(db):
    sid = _sede(db, 8, "SLP")
    resp = w_create.handler(_ev({"company_id": 9, "name": "General", "site_id": sid}), None)
    assert resp["statusCode"] == 422


def test_mismo_nombre_de_almacen_en_sedes_distintas_si_se_puede(db):
    a, b = _sede(db, 8, "SLP"), _sede(db, 8, "ZUM")
    _almacen(db, 8, "Desecho", a)
    _almacen(db, 8, "Desecho", b)
    resp = w_create.handler(_ev({"company_id": 8, "name": "Desecho", "site_id": a}), None)
    assert resp["statusCode"] == 409


def test_almacen_sin_sede_es_422(db):
    resp = w_create.handler(_ev({"company_id": 2, "name": "Inventario Quinta"}), None)
    assert resp["statusCode"] == 422
    resp = w_create.handler(_ev({"company_id": 2, "name": "Inventario Quinta",
                                 "site_id": None}), None)
    assert resp["statusCode"] == 422


def test_sede_inexistente_es_422(db):
    resp = w_create.handler(_ev({"company_id": 8, "name": "General", "site_id": 999}), None)
    assert resp["statusCode"] == 422


def test_listado_trae_los_almacenes_dentro_de_su_sede(db):
    sid = _sede(db, 2, "Matriz")
    _almacen(db, 2, "Inventario Quinta", sid)
    out = _ok(s_list.handler(_ev(company_id=2), None))
    assert "unassigned_warehouses" not in out
    assert [w["name"] for w in out["sites"][0]["warehouses"]] == ["Inventario Quinta"]


def test_mover_almacen_de_sede_pero_no_sacarlo(db):
    a, b = _sede(db, 8, "SLP"), _sede(db, 8, "ZUM")
    wid = _almacen(db, 8, "General", a)
    rec = _ok(w_update.handler(_ev({"site_id": b}, id=wid), None))
    assert rec["site_id"] == b and rec["yard"] == "ZUM"
    assert w_update.handler(_ev({"site_id": None}, id=wid), None)["statusCode"] == 422
    assert db.fila("warehouses", wid)["site_id"] == b
    # Sin site_id en el cuerpo no se toca la sede.
    rec = _ok(w_update.handler(_ev({"type": "scrap"}, id=wid), None))
    assert rec["site_id"] == b and rec["type"] == "scrap"


def test_almacen_no_se_mueve_a_sede_de_otra_compania(db):
    a = _sede(db, 8, "SLP")
    ajena = _sede(db, 9, "MTY")
    wid = _almacen(db, 8, "General", a)
    assert w_update.handler(_ev({"site_id": ajena}, id=wid), None)["statusCode"] == 422


def test_tipo_invalido_es_422(db):
    resp = w_create.handler(_ev({"company_id": 8, "name": "X", "type": "bodega"}), None)
    assert resp["statusCode"] == 422


# ─── Activos a almacenes ─────────────────────────────────────────────────────

def _inventario(db, company_id=8):
    db.sembrar("tires", id=10, company_id=company_id)
    db.sembrar("sensors", id=20, company_id=company_id)
    db.sembrar("tboxes", id=30, company_id=company_id)


def test_asignar_llantas_sensores_y_qbox(db):
    _inventario(db)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "tire_ids": [10], "sensor_ids": [20],
                              "tbox_ids": [30]}), None))
    for tabla, rid in (("tires", 10), ("sensors", 20), ("tboxes", 30)):
        assert db.fila(tabla, rid)["warehouse_id"] == wid
    w = _ok(s_list.handler(_ev(company_id=8), None))["sites"][0]["warehouses"][0]
    assert (w["tire_count"], w["sensor_count"], w["tbox_count"]) == (1, 1, 1)


def test_activo_de_otra_compania_no_entra_y_no_se_mueve_ninguno(db):
    _inventario(db)
    db.sembrar("sensors", id=21, company_id=9)
    wid = _almacen(db, 8, "General")
    resp = w_assign.handler(_ev({"warehouse_id": wid, "tire_ids": [10],
                                 "sensor_ids": [20, 21]}), None)
    assert resp["statusCode"] == 422
    assert json.loads(resp["body"])["error"]["sensor_ids"] == [21]
    assert db.fila("tires", 10)["warehouse_id"] is None


def test_sacar_del_almacen(db):
    _inventario(db)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "tire_ids": [10]}), None))
    _ok(w_assign.handler(_ev({"warehouse_id": None, "tire_ids": [10]}), None))
    assert db.fila("tires", 10)["warehouse_id"] is None


def test_asignacion_vacia_es_422(db):
    wid = _almacen(db, 8, "General")
    assert w_assign.handler(_ev({"warehouse_id": wid}), None)["statusCode"] == 422


def test_cliente_no_mete_cosas_al_almacen_de_otro(db):
    _inventario(db, company_id=9)
    wid = _almacen(db, 8, "General")
    resp = w_assign.handler(_ev({"warehouse_id": wid, "tire_ids": [10]}, company_id=9), None)
    assert resp["statusCode"] == 404


def test_no_se_borra_un_almacen_con_inventario(db):
    _inventario(db)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "tbox_ids": [30]}), None))
    resp = w_delete.handler(_ev(id=wid), None)
    assert resp["statusCode"] == 409 and "1 Qbox" in json.loads(resp["body"])["error"]


def test_almacen_con_historial_de_quinta_1_no_se_borra(db):
    wid = _almacen(db, 8, "Viejo")
    db.sembrar("warehouse_tires", id=1, warehouse_id=wid, tire_id=10)
    assert w_delete.handler(_ev(id=wid), None)["statusCode"] == 409
    assert db.fila("warehouses", wid) is not None


# ─── Listados ────────────────────────────────────────────────────────────────

def test_list_assets_filtra_por_almacen(db, monkeypatch):
    db.sembrar("tires", id=10, company_id=8)
    db.sembrar("tires", id=11, company_id=8)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "tire_ids": [11]}), None))
    # El esquema de prueba solo tiene las columnas de ubicación.
    monkeypatch.setitem(list_assets.RESOURCES["tires"], "columns", "id, company_id, warehouse_id")
    ev = {"pathParameters": {"resource": "tires"},
          "queryStringParameters": {"warehouse_id": str(wid)}}
    assert [r["id"] for r in _ok(list_assets.handler(ev, None))] == [11]


def test_list_assets_expone_la_ubicacion():
    cols = {k: v["columns"] for k, v in list_assets.RESOURCES.items()}
    assert "site_id" in cols["units"]
    for r in ("tires", "sensors", "tboxes"):
        assert "warehouse_id" in cols[r]
    assert "sites" in cols and "warehouses" in cols


# ─── Cambio de compañía ──────────────────────────────────────────────────────

def test_sensor_y_qbox_que_cambian_de_compania_dejan_el_almacen(db):
    _inventario(db)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "sensor_ids": [20], "tbox_ids": [30]}), None))
    _ok(sen_assign.handler(_ev({"company_id": 9}, id=20), None))
    _ok(tb_assign.handler(_ev({"company_id": 9}, id=30), None))
    assert db.fila("sensors", 20)["warehouse_id"] is None
    assert db.fila("tboxes", 30)["warehouse_id"] is None


def test_reasignar_a_la_misma_compania_conserva_el_almacen(db):
    _inventario(db)
    wid = _almacen(db, 8, "General")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "sensor_ids": [20]}), None))
    _ok(sen_assign.handler(_ev({"company_id": 8}, id=20), None))
    assert db.fila("sensors", 20)["warehouse_id"] == wid


def test_mover_paquete_saca_su_qbox_y_sensores_del_almacen(db):
    db.sembrar("packages", id=1, company_id=2, name="Kit", status="prepared")
    db.sembrar("tboxes", id=30, company_id=2, package_id=1)
    db.sembrar("sensors", id=20, company_id=2, package_id=1)
    wid = _almacen(db, 2, "Paquetes")
    _ok(w_assign.handler(_ev({"warehouse_id": wid, "sensor_ids": [20], "tbox_ids": [30]}), None))
    _ok(p_move.handler(_ev({"company_id": 8}, id=1), None))
    for tabla, rid in (("tboxes", 30), ("sensors", 20)):
        fila = db.fila(tabla, rid)
        assert fila["company_id"] == 8 and fila["warehouse_id"] is None
