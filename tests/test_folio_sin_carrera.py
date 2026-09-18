"""
test_folio_sin_carrera.py
~~~~~~~~~~~~~~~~~~~~~~~~~
El folio es único por compañía, pero eso lo dice el código, no el índice.

El índice de la tabla es (prefix, folio, company_id), así que la base deja pasar
el repetido si el prefijo difiere. Mientras siga así, dos requests simultáneos
pueden mirar los dos, ver el folio libre los dos, y escribir los dos: la
comprobación y la escritura no son un solo acto.

El lock nombrado por folio+compañía cierra esa ventana. Es la red mientras el
índice no se pueda apretar (hoy no se puede: hay 32 folios repetidos vivos).
"""
import json

import pytest

from functions.tires import create as tires_create
from functions.tires import update as tires_update


class DBQueRegistraLocks:
    """Doble de conexión que anota los GET_LOCK/RELEASE_LOCK que pide el handler."""

    def __init__(self):
        self.locks = []
        self.liberados = []

    class _Cur:
        def __init__(self, dueno):
            self.dueno = dueno

        def execute(self, sql, params=()):
            if "GET_LOCK" in sql:
                self.dueno.locks.append(params[0])
            elif "RELEASE_LOCK" in sql:
                self.dueno.liberados.append(params[0])

        def fetchone(self):
            return (1,)

    def cursor(self):
        return DBQueRegistraLocks._Cur(self)

    def commit(self): pass
    def rollback(self): pass


@pytest.fixture
def db_locks(monkeypatch):
    db = DBQueRegistraLocks()
    for mod in (tires_create, tires_update):
        monkeypatch.setattr(mod, "get_db", lambda: db)
    return db


def test_el_alta_serializa_por_folio_y_compania(db_locks, monkeypatch):
    monkeypatch.setattr(tires_create, "get_by_id",
                        lambda db, table, rid: {"id": rid} if table == "tires_catalog" else None)
    monkeypatch.setattr(tires_create, "get_where", lambda *a, **k: [])
    monkeypatch.setattr(tires_create, "insert",
                        lambda db, table, row: {"id": 99, **row})
    monkeypatch.setattr(tires_create, "SmartTyreClient",
                        lambda: (_ for _ in ()).throw(ConnectionError("da igual")))

    tires_create.handler({"body": json.dumps(
        {"prefix": "TSM", "folio": "9001", "company_id": 100,
         "tires_catalog_id": 209})}, None)

    assert "folio:100:9001" in db_locks.locks, "el alta no serializa el folio"
    assert db_locks.liberados == db_locks.locks, "el lock no se soltó"


def test_el_editar_toma_el_MISMO_lock_que_el_alta(db_locks, monkeypatch):
    """Tienen que compartir llave: si cada uno tomara la suya, un alta y una
    edición del mismo folio correrían en paralelo y la regla se cae igual."""
    monkeypatch.setattr(tires_update, "get_by_id",
                        lambda db, table, rid: {"id": rid, "is_deleted": 0,
                                                "folio": "202", "company_id": 100})
    monkeypatch.setattr(tires_update, "get_where", lambda *a, **k: [])
    monkeypatch.setattr(tires_update, "update",
                        lambda db, table, rid, data: {"id": rid, **data})
    monkeypatch.setattr(tires_update, "audit", lambda *a, **k: None)

    tires_update.handler({"pathParameters": {"id": "1"},
                          "body": json.dumps({"folio": "9001"})}, None)

    assert "folio:100:9001" in db_locks.locks
    assert db_locks.liberados == db_locks.locks


def test_el_lock_se_suelta_aunque_truene_dentro(db_locks, monkeypatch):
    """Un lock de MySQL vive mientras viva la conexión, y la conexión se reutiliza
    entre invocaciones del mismo contenedor: no soltarlo cuelga a los siguientes."""
    monkeypatch.setattr(tires_update, "get_by_id",
                        lambda db, table, rid: {"id": rid, "is_deleted": 0,
                                                "folio": "202", "company_id": 100})
    monkeypatch.setattr(tires_update, "get_where", lambda *a, **k: [])
    monkeypatch.setattr(tires_update, "update",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db caída")))

    resp = tires_update.handler({"pathParameters": {"id": "1"},
                                 "body": json.dumps({"folio": "9001"})}, None)

    assert resp["statusCode"] == 500
    assert db_locks.liberados == db_locks.locks, "se quedó el lock tomado tras el error"
