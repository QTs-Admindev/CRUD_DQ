import pymysql.cursors

from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_in
from shared.operadores import DRIVERS, asignaciones, nombre_de
from shared.utils.response import error, ok

LIMITE_VIGENTES = 5000
LIMITE_HISTORIAL = 200
TRUE_VALUES = {"1", "true", "True", "yes"}


def handler(event, context):
    # GET /driver-assignments?company_id=..&unit_id=..&driver_id=..&history=1
    # Sin history: las asignaciones vigentes (una por unidad), para las tablas de
    # unidades y operadores. Con history=1 y unit_id o driver_id: su historial,
    # lo más reciente primero.
    qs = event.get("queryStringParameters") or {}
    try:
        filtros = {k: int(qs[k]) for k in ("company_id", "unit_id", "driver_id") if qs.get(k)}
    except ValueError:
        return error(422, "company_id, unit_id y driver_id deben ser enteros")
    historial = str(qs.get("history") or "") in TRUE_VALUES
    if historial and not ("unit_id" in filtros or "driver_id" in filtros):
        return error(422, "history=1 requiere unit_id o driver_id")
    if filtros.get("company_id") == ADMIN_COMPANY_ID:
        del filtros["company_id"]

    condiciones = [f"{k} = %s" for k in filtros]
    if not historial:
        condiciones.append("released_at IS NULL")
    where = " AND ".join(condiciones) or "1 = 1"
    limite = LIMITE_HISTORIAL if historial else LIMITE_VIGENTES

    db = get_db()
    try:
        with db.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(f"SELECT id, company_id, unit_id, driver_id, assigned_at, assigned_by, "
                        f"released_at, released_by FROM {asignaciones()} WHERE {where} "
                        f"ORDER BY assigned_at DESC, id DESC LIMIT {limite}",
                        list(filtros.values()))
            filas = list(cur.fetchall())
        operadores = {d["id"]: d for d in get_in(
            db, DRIVERS, "id", {f["driver_id"] for f in filas},
            "id, name, last_name, second_last_name, phone_number, is_active")}
        unidades = {u["id"]: u for u in get_in(
            db, t("units"), "id", {f["unit_id"] for f in filas}, "id, unit_identifier")}
    except Exception as e:
        return error(500, f"DB error (asignaciones de operadores): {e}")

    for f in filas:
        operador = operadores.get(f["driver_id"])
        f["driver_name"] = nombre_de(operador)
        f["driver_active"] = bool(operador and operador.get("is_active"))
        f["unit_identifier"] = (unidades.get(f["unit_id"]) or {}).get("unit_identifier")
    return ok(filas)
