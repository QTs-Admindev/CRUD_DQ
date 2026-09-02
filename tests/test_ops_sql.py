"""Tests del SQL que arma `shared/db/ops.py`.

El resto de la suite mockea `get_many`, así que verifica que el handler le pase los
argumentos correctos pero nunca que la función los USE. Sin esto, borrar el OFFSET del
SQL deja las 279 pruebas en verde y el síntoma no es un error: el que pagina recibe
siempre el primer bloque, así que arma un inventario truncado y duplicado que parece
completo.

Aquí se inspecciona el SQL generado con un cursor falso, sin base de datos.
"""
from shared.db import ops


class FakeCursor:
    """Cursor que registra lo ejecutado y devuelve filas programadas."""

    def __init__(self, rows=None, one=None):
        self.rows = rows if rows is not None else []
        self.one = one
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), list(params or [])))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


class FakeDB:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass


def _sql(cursor):
    return cursor.executed[-1][0]


def _params(cursor):
    return cursor.executed[-1][1]


# ------------------------------------------------------------------ get_many

def test_get_many_applies_offset_to_the_sql():
    cur = FakeCursor(rows=[{"id": 1}])
    ops.get_many(FakeDB(cur), "tires", "id", None, limit=1000, offset=2000)
    assert "LIMIT 1000" in _sql(cur)
    assert "OFFSET 2000" in _sql(cur)


def test_get_many_without_offset_does_not_emit_offset():
    cur = FakeCursor()
    ops.get_many(FakeDB(cur), "tires", "id", None, limit=300)
    assert "LIMIT 300" in _sql(cur)
    assert "OFFSET" not in _sql(cur)


def test_get_many_orders_by_id_desc():
    # El orden estable es lo que hace que paginar por offset tenga sentido.
    cur = FakeCursor()
    ops.get_many(FakeDB(cur), "sensors", "id", None, limit=10)
    assert "ORDER BY id DESC" in _sql(cur)


def test_get_many_builds_the_where_with_placeholders():
    cur = FakeCursor()
    ops.get_many(FakeDB(cur), "tires", "id, folio",
                 {"is_deleted": 0, "company_id": 100}, limit=50)
    sql = _sql(cur)
    assert "SELECT id, folio FROM tires WHERE" in sql
    assert "is_deleted = %s" in sql and "company_id = %s" in sql
    assert _params(cur) == [0, 100]      # valores por parámetro, no interpolados


def test_get_many_pages_do_not_overlap():
    # Dos páginas consecutivas piden ventanas distintas del mismo orden.
    cur = FakeCursor()
    db = FakeDB(cur)
    ops.get_many(db, "tires", "id", None, limit=5000, offset=0)
    first = _sql(cur)
    ops.get_many(db, "tires", "id", None, limit=5000, offset=5000)
    second = _sql(cur)
    assert "OFFSET" not in first
    assert "OFFSET 5000" in second


# ---------------------------------------------------------------- count_rows

def test_count_rows_uses_the_same_filters_as_the_page():
    # Si el total se contara con otros filtros, el que pagina nunca terminaría
    # (o cortaría antes de tiempo) sin que nada falle.
    cur = FakeCursor(one={"n": 3400})
    total = ops.count_rows(FakeDB(cur), "tires", {"is_deleted": 0, "company_id": 100})
    assert total == 3400
    sql = _sql(cur)
    assert sql.startswith("SELECT COUNT(*) AS n FROM tires WHERE")
    assert "is_deleted = %s" in sql and "company_id = %s" in sql
    assert _params(cur) == [0, 100]


def test_count_rows_without_filters_counts_everything():
    cur = FakeCursor(one={"n": 12})
    assert ops.count_rows(FakeDB(cur), "sensors") == 12
    assert "WHERE" not in _sql(cur)


def test_count_rows_handles_an_empty_result():
    cur = FakeCursor(one=None)
    assert ops.count_rows(FakeDB(cur), "sensors", {"company_id": 7}) == 0
