"""Operador asignado a cada unidad.

Corre el SQL real de los handlers sobre SQLite en memoria, con los mismos índices
UNIQUE sobre columnas generadas que la migración. Lo que se protege:
  1. una unidad con un solo operador y un operador con una sola unidad
  2. que no se mezclen compañías
  3. que el historial quede completo y la bitácora diga quién lo hizo
"""
import json
import sqlite3

import pymysql
import pytest

from functions.drivers import assign as d_assign
from functions.drivers import list as d_list
from functions.vehicles import delete as vdel

ESQUEMA = """
CREATE TABLE companies (id INTEGER PRIMARY KEY, company_name TEXT);
CREATE TABLE units (id INTEGER PRIMARY KEY, company_id INT, unit_identifier TEXT,
  daijin_id TEXT, deleting_at INT, is_deleted INT DEFAULT 0, updated_at INT);
CREATE TABLE drivers (id INTEGER PRIMARY KEY, company_id INT, name TEXT, last_name TEXT,
  second_last_name TEXT, phone_number TEXT, is_active INT DEFAULT 1);
CREATE TABLE driver_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INT NOT NULL, unit_id INT NOT NULL, driver_id INT NOT NULL,
  assigned_at INT NOT NULL, assigned_by TEXT, released_at INT, released_by TEXT,
  active_unit_id INT GENERATED ALWAYS AS (CASE WHEN released_at IS NULL THEN unit_id END) STORED,
  active_driver_id INT GENERATED ALWAYS AS (CASE WHEN released_at IS NULL THEN driver_id END) STORED);
CREATE UNIQUE INDEX uq_da_active_unit ON driver_assignments (active_unit_id);
CREATE UNIQUE INDEX uq_da_active_driver ON driver_assignments (active_driver_id);
CREATE TABLE packages (id INTEGER PRIMARY KEY, unit_id INT, status TEXT, updated_at INT);
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
            raise pymysql.err.IntegrityError(1062 if "UNIQUE" in str(e) else 1451, str(e))

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

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
        self.conn.executescript(ESQUEMA)

    def cursor(self, clase=None):
        return _Cursor(self.conn, clase is pymysql.cursors.DictCursor)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def filas(self, sql):
        return self.conn.execute(sql).fetchall()

    def sembrar(self, tabla, **campos):
        marcas = ", ".join(["?"] * len(campos))
        cur = self.conn.execute(f"INSERT INTO {tabla} ({', '.join(campos)}) VALUES ({marcas})",
                                list(campos.values()))
        self.conn.commit()
        return cur.lastrowid


@pytest.fixture
def db(monkeypatch):
    base = SQLiteDB()
    for m in (d_assign, d_list):
        monkeypatch.setattr(m, "get_db", lambda: base)
    for cid in (2, 8, 9):
        base.sembrar("companies", id=cid, company_name=f"C{cid}")
    base.sembrar("units", id=10, company_id=8, unit_identifier="T-10")
    base.sembrar("units", id=11, company_id=8, unit_identifier="T-11")
    base.sembrar("units", id=20, company_id=9, unit_identifier="B-20")
    base.sembrar("drivers", id=1, company_id=8, name="Ana", last_name="Ruiz", phone_number="+5215500000001")
    base.sembrar("drivers", id=2, company_id=8, name="Beto", phone_number="+5215500000002")
    base.sembrar("drivers", id=3, company_id=9, name="Ciro", phone_number="+5215500000003")
    base.sembrar("drivers", id=4, company_id=8, name="Dora", phone_number="+5215500000004", is_active=0)
    return base


def _put(unit_id, body, company_id=8, actor="ops@cliente.mx"):
    ev = {"pathParameters": {"id": str(unit_id)}, "body": json.dumps(body),
          "headers": {"X-Actor": actor}}
    if company_id is not None:
        ev["queryStringParameters"] = {"company_id": str(company_id)}
    return d_assign.handler(ev, None)


def _get(**qs):
    return d_list.handler({"queryStringParameters": {k: str(v) for k, v in qs.items()}}, None)


def _ok(resp, status=200):
    assert resp["statusCode"] == status, resp["body"]
    return json.loads(resp["body"])


def _vigentes(db):
    return db.filas("SELECT unit_id, driver_id FROM driver_assignments "
                    "WHERE released_at IS NULL ORDER BY unit_id")


def test_asigna_operador_y_deja_bitacora(db):
    r = _ok(_put(10, {"driver_id": 1}))
    assert r["driver_name"] == "Ana Ruiz" and r["unit_identifier"] == "T-10" and r["changed"]
    assert _vigentes(db) == [(10, 1)]
    assert db.filas("SELECT actor, asset_id, changes FROM asset_audit_log") == [
        ("ops@cliente.mx", 10, json.dumps({"driver_id": [None, 1]}))]


def test_reasignar_la_unidad_cierra_la_anterior(db):
    _ok(_put(10, {"driver_id": 1}))
    _ok(_put(10, {"driver_id": 2}))
    assert _vigentes(db) == [(10, 2)]
    historial = _ok(_get(company_id=8, unit_id=10, history=1))
    assert [h["driver_id"] for h in historial] == [2, 1]
    assert historial[1]["released_at"] and historial[1]["released_by"] == "ops@cliente.mx"


def test_mismo_operador_otra_vez_no_cambia_nada(db):
    _ok(_put(10, {"driver_id": 1}))
    r = _ok(_put(10, {"driver_id": 1}))
    assert r["changed"] is False
    assert len(db.filas("SELECT id FROM driver_assignments")) == 1


def test_operador_en_otra_unidad_pide_confirmacion_y_con_move_se_pasa(db):
    _ok(_put(10, {"driver_id": 1}))
    r = _put(11, {"driver_id": 1})
    assert r["statusCode"] == 409
    assert json.loads(r["body"])["error"]["unit_identifier"] == "T-10"
    assert _vigentes(db) == [(10, 1)]

    _ok(_put(11, {"driver_id": 1, "move": True}))
    assert _vigentes(db) == [(11, 1)]


def test_quitar_operador(db):
    _ok(_put(10, {"driver_id": 1}))
    assert _ok(_put(10, {"driver_id": None}))["changed"] is True
    assert _vigentes(db) == []
    assert _ok(_put(10, {"driver_id": None}))["changed"] is False


def test_no_mezcla_companias(db):
    # Operador de la 9 en unidad de la 8.
    assert _put(10, {"driver_id": 3}, company_id=2)["statusCode"] == 422
    # La 9 no ve la unidad de la 8 ni la 8 al operador de la 9.
    assert _put(10, {"driver_id": 3}, company_id=9)["statusCode"] == 404
    assert _put(20, {"driver_id": 3}, company_id=8)["statusCode"] == 404
    assert _vigentes(db) == []


def test_operador_dado_de_baja_no_se_asigna(db):
    assert _put(10, {"driver_id": 4})["statusCode"] == 422
    assert _put(10, {"driver_id": 99})["statusCode"] == 404


def test_unidad_borrada_o_borrandose(db):
    db.conn.execute("UPDATE units SET is_deleted = 1 WHERE id = 10")
    db.conn.execute("UPDATE units SET deleting_at = 1 WHERE id = 11")
    assert _put(10, {"driver_id": 1})["statusCode"] == 404
    assert _put(11, {"driver_id": 1})["statusCode"] == 409


def test_carrera_entre_dos_asignaciones_responde_409(db):
    # Otra petición dejó al operador 1 en la unidad 11 entre la lectura y la escritura.
    db.sembrar("driver_assignments", company_id=8, unit_id=11, driver_id=1, assigned_at=1)
    original = d_assign.vigente
    d_assign.vigente = lambda db_, campo, valor: None
    try:
        assert _put(10, {"driver_id": 1})["statusCode"] == 409
    finally:
        d_assign.vigente = original
    assert _vigentes(db) == [(11, 1)]


def test_lista_vigentes_por_compania(db):
    _ok(_put(10, {"driver_id": 1}))
    _ok(_put(20, {"driver_id": 3}, company_id=9))
    _ok(_put(10, {"driver_id": 2}))
    de_8 = _ok(_get(company_id=8))
    assert [(a["unit_id"], a["driver_name"], a["unit_identifier"]) for a in de_8] == [(10, "Beto", "T-10")]
    assert len(_ok(_get(company_id=2))) == 2
    assert _get(company_id=8, history=1)["statusCode"] == 422


def test_borrar_la_unidad_libera_al_operador_y_retira_su_paquete(db):
    _ok(_put(10, {"driver_id": 1}))
    db.sembrar("packages", id=5, unit_id=10, status="assigned")
    db.sembrar("packages", id=6, unit_id=11, status="assigned")
    vdel.soltar_unidad_borrada(db, 10, 123, "ops@cliente.mx")
    db.commit()
    assert _vigentes(db) == []
    assert db.filas("SELECT id, status FROM packages ORDER BY id") == [(5, "retired"), (6, "assigned")]
    _ok(_put(11, {"driver_id": 1}))
    assert _vigentes(db) == [(11, 1)]


def test_asignacion_colgada_de_unidad_borrada_no_estorba(db):
    _ok(_put(10, {"driver_id": 1}))
    db.conn.execute("UPDATE units SET is_deleted = 1 WHERE id = 10")
    _ok(_put(11, {"driver_id": 1}))
    assert _vigentes(db) == [(11, 1)]


def test_borrar_unidad_sin_la_tabla_no_falla():
    class SinTabla:
        def cursor(self, *a):
            raise pymysql.err.ProgrammingError(1146, "Table 'driver_assignments' doesn't exist")
    vdel.soltar_unidad_borrada(SinTabla(), 10, 123, None)
