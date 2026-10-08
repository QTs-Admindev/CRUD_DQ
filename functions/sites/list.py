import pymysql.cursors

from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_many
from shared.utils.response import error, ok

LIMITE = 5000


def handler(event, context):
    # GET /sites?company_id=.. -> sedes con sus almacenes y cuántos activos tiene
    # cada uno, todo en una respuesta para la pantalla de sedes y almacenes.
    # La compañía admin (o sin company_id) ve las de todas; las demás, las suyas.
    # Los almacenes sin sede van aparte, en `unassigned_warehouses`.
    qs = event.get("queryStringParameters") or {}
    filtros: dict = {}
    if qs.get("company_id"):
        try:
            company_id = int(qs["company_id"])
        except ValueError:
            return error(422, "company_id must be an integer")
        if company_id != ADMIN_COMPANY_ID:
            filtros["company_id"] = company_id

    db = get_db()
    try:
        sedes = get_many(db, t("sites"), "id, company_id, name, created_at, updated_at",
                         filtros, limit=LIMITE)
        almacenes = get_many(db, t("warehouses"),
                             "id, company_id, site_id, name, type, created_at, updated_at",
                             filtros, limit=LIMITE)
        unidades = _contar(db, "units", "site_id", [s["id"] for s in sedes])
        ids_alm = [w["id"] for w in almacenes]
        llantas = _contar(db, "tires", "warehouse_id", ids_alm)
        sensores = _contar(db, "sensors", "warehouse_id", ids_alm)
        qbox = _contar(db, "tboxes", "warehouse_id", ids_alm)
    except Exception as e:
        return error(500, f"DB error (list sites): {e}")

    por_sede: dict = {}
    sueltos = []
    for w in sorted(almacenes, key=lambda w: (w["name"] or "").lower()):
        w["tire_count"] = llantas.get(w["id"], 0)
        w["sensor_count"] = sensores.get(w["id"], 0)
        w["tbox_count"] = qbox.get(w["id"], 0)
        if w.get("site_id"):
            por_sede.setdefault(w["site_id"], []).append(w)
        else:
            sueltos.append(w)

    for s in sedes:
        s["unit_count"] = unidades.get(s["id"], 0)
        s["warehouses"] = por_sede.get(s["id"], [])
    sedes.sort(key=lambda s: (s["company_id"], (s["name"] or "").lower()))
    return ok({"sites": sedes, "unassigned_warehouses": sueltos})


def _contar(db, tabla: str, columna: str, ids: list) -> dict:
    """{id de sede/almacén: activos vivos que tiene}. Una consulta por tabla."""
    if not ids:
        return {}
    marcas = ", ".join(["%s"] * len(ids))
    sql = (f"SELECT {columna} AS k, COUNT(*) AS n FROM {t(tabla)} "
           f"WHERE {columna} IN ({marcas}) AND (is_deleted IS NULL OR is_deleted = 0) "
           f"GROUP BY {columna}")
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, list(ids))
        return {r["k"]: int(r["n"]) for r in cur.fetchall()}
