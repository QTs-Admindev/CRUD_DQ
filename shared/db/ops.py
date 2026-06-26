import pymysql.cursors


def insert(db, table: str, data: dict) -> dict:
    columns = list(data.keys())
    placeholders = ["%s"] * len(columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
    with db.cursor() as cur:
        cur.execute(sql, list(data.values()))
        last_id = cur.lastrowid
    return get_by_id(db, table, last_id)


def update(db, table: str, record_id: int, data: dict) -> dict:
    sets = [f"{k} = %s" for k in data.keys()]
    sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s"
    with db.cursor() as cur:
        cur.execute(sql, list(data.values()) + [record_id])
    return get_by_id(db, table, record_id)


def get_by_id(db, table: str, record_id: int) -> dict | None:
    sql = f"SELECT * FROM {table} WHERE id = %s"
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, [record_id])
        return cur.fetchone()


def get_by_field(db, table: str, field: str, value) -> dict | None:
    sql = f"SELECT * FROM {table} WHERE {field} = %s"
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, [value])
        return cur.fetchone()


def get_many(db, table: str, columns: str = "*", filters: dict | None = None,
             limit: int = 300) -> list[dict]:
    """Lista filas (para poblar selects del tester). Orden id DESC para ver lo reciente."""
    sql = f"SELECT {columns} FROM {table}"
    vals: list = []
    if filters:
        sql += " WHERE " + " AND ".join(f"{k} = %s" for k in filters)
        vals = list(filters.values())
    sql += f" ORDER BY id DESC LIMIT {int(limit)}"
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, vals)
        return list(cur.fetchall())


def soft_delete(db, table: str, record_id: int) -> dict | None:
    """Soft delete: marca is_deleted=1 sin borrar la fila (conserva historial + daijin_id)."""
    sql = f"UPDATE {table} SET is_deleted = 1 WHERE id = %s"
    with db.cursor() as cur:
        cur.execute(sql, [record_id])
    return get_by_id(db, table, record_id)


def get_by_fields(db, table: str, filters: dict) -> dict | None:
    """Busca por una llave compuesta (varios campos AND). Para natural keys como
    (prefix, folio, company_id) en tires o (unit_identifier, company_id, unit_catalog_id)."""
    where = " AND ".join(f"{k} = %s" for k in filters)
    sql = f"SELECT * FROM {table} WHERE {where} LIMIT 1"
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, list(filters.values()))
        return cur.fetchone()
