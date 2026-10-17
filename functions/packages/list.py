from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_in, get_many
from shared.utils.response import error, ok

DEFAULT_LIMIT = 300
MAX_LIMIT = 5000

COLUMNS = ("id, name, unit_catalog_id, company_id, unit_id, status, "
           "assign_done, assign_total, assign_error, created_at, updated_at")


def handler(event, context):
    # GET /packages -> lista de paquetes. Filtros opcionales por query string:
    #   ?company_id=..  (la compañía admin ve todo), ?status=prepared|assigned|retired
    # Sin status no salen los retirados ni los paquetes de una unidad borrada.
    qs = event.get("queryStringParameters") or {}

    filters: dict = {}
    if qs.get("company_id"):
        try:
            company_id = int(qs["company_id"])
        except ValueError:
            return error(422, "company_id must be an integer")
        # La compañía admin ve todos los paquetes; el resto solo los suyos.
        if company_id != ADMIN_COMPANY_ID:
            filters["company_id"] = company_id
    if qs.get("status"):
        filters["status"] = qs["status"]

    limit = DEFAULT_LIMIT
    if qs.get("limit"):
        try:
            limit = max(1, min(int(qs["limit"]), MAX_LIMIT))
        except ValueError:
            return error(422, "limit must be an integer")

    db = get_db()
    try:
        # Se trae el tope y se filtra aquí, así lo oculto no se come el límite.
        rows = get_many(db, t("packages"), COLUMNS, filters, limit=MAX_LIMIT)
        if not qs.get("status"):
            rows = _vigentes(db, rows)
        return ok(_enrich(db, rows[:limit]))
    except Exception as e:
        return error(500, f"DB error (list packages): {e}")


def _vigentes(db, rows: list[dict]) -> list[dict]:
    # Lo borrado no se muestra: ni los paquetes retirados ni los de una unidad borrada.
    rows = [r for r in rows if r.get("status") != "retired"]
    unit_ids = {r["unit_id"] for r in rows if r.get("unit_id") is not None}
    borradas = {u["id"] for u in get_in(db, t("units"), "id", unit_ids, "id, is_deleted")
                if u.get("is_deleted")}
    return [r for r in rows if r.get("unit_id") not in borradas]


def _enrich(db, rows: list[dict]) -> list[dict]:
    # Enriquecer cada paquete con lo que el FE necesita en la lista:
    #   - sensor_count: cuántos sensores cuelgan del paquete (sellados con su package_id).
    #   - tboxCode: el código del tbox del paquete (o None si aún no tiene).
    # Se resuelve con DOS consultas IN sobre todos los ids a la vez (no N+1).
    ids = [r["id"] for r in rows]
    if not ids:
        return rows

    counts: dict = {}
    for s in get_in(db, t("sensors"), "package_id", ids, "id, package_id, is_deleted"):
        if s.get("is_deleted"):
            continue
        pid = s.get("package_id")
        counts[pid] = counts.get(pid, 0) + 1

    tbox_by_pkg: dict = {}
    for tb in get_in(db, t("tboxes"), "package_id", ids, "package_id, tboxCode, is_deleted"):
        if not tb.get("is_deleted"):
            tbox_by_pkg[tb.get("package_id")] = tb.get("tboxCode")

    for r in rows:
        r["sensor_count"] = counts.get(r["id"], 0)
        r["tboxCode"] = tbox_by_pkg.get(r["id"])
    return rows
