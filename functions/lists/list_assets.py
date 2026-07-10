from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_many
from shared.utils.response import error, ok

DEFAULT_LIMIT = 300
MAX_LIMIT = 5000

# Whitelist recurso -> columnas + comportamiento.
#   prefixed: la tabla usa TABLE_PREFIX (activos); los catálogos son tablas REALES.
#   soft: tiene is_deleted (nunca listamos borrados).
#   by_company: acepta filtro company_id (admin ve todo).
RESOURCES = {
    "units": {
        "columns": ("id, unit_identifier, company_id, daijin_id, status, tbox_id, "
                    "unit_catalog_id, vin, plates, mileage, "
                    "created_at, updated_at"),
        "prefixed": True, "soft": True, "by_company": True,
    },
    "tires": {
        "columns": ("id, prefix, folio, company_id, daijin_id, status, "
                    "unit_id, sensor_id, is_mounted, axle_index, wheel_index, "
                    "mount_position, tires_catalog_id, current_depth, tire_mileage, "
                    "life_number, cost, created_at, updated_at"),
        "prefixed": True, "soft": True, "by_company": True,
    },
    "sensors": {
        "columns": "id, sensorCode, company_id, daijin_id, status",
        "prefixed": True, "soft": True, "by_company": True,
    },
    "tboxes": {
        "columns": "id, tboxCode, version, company_id, daijin_id, status",
        "prefixed": True, "soft": True, "by_company": True,
    },
    # Catálogos / referencia (tablas reales, solo lectura, sin soft-delete).
    "unit_catalog": {
        "columns": "id, name, type, axles_count, total_tires",
        "prefixed": False, "soft": False, "by_company": False,
    },
    "tires_catalog": {
        "columns": "id, brand, model, size, position, max_depth, min_depth",
        "prefixed": False, "soft": False, "by_company": False,
    },
    "companies": {
        "columns": "id, company_name, finance_name",
        "prefixed": False, "soft": False, "by_company": False,
    },
    # Bitácora de auditoría (tabla real, solo lectura). Filtrable por actor/action/
    # asset_type/asset_id/result/company_id vía los filtros opcionales de abajo.
    "asset_audit_log": {
        "columns": ("id, request_id, actor, action, asset_type, asset_id, "
                    "natural_key, company_id, daijin_id, result, payload, "
                    "changes, error, created_at"),
        "prefixed": False, "soft": False, "by_company": True,
    },
}


def handler(event, context):
    resource = (event.get("pathParameters") or {}).get("resource")
    cfg = RESOURCES.get(resource)
    if not cfg:
        return error(404, f"recurso inválido: {resource} (usa {list(RESOURCES)})")

    qs = event.get("queryStringParameters") or {}

    filters = {}
    if cfg["soft"]:
        filters["is_deleted"] = 0  # soft-deleted rows are never listed
    if cfg["by_company"] and qs.get("company_id"):
        try:
            company_id = int(qs["company_id"])
        except ValueError:
            return error(422, "company_id must be an integer")
        # Admin company sees everything (incl. unassigned inventory); others only their own.
        if company_id != ADMIN_COMPANY_ID:
            filters["company_id"] = company_id

    limit = DEFAULT_LIMIT
    if qs.get("limit"):
        try:
            limit = max(1, min(int(qs["limit"]), MAX_LIMIT))
        except ValueError:
            return error(422, "limit must be an integer")

    # Extra optional filters (backward compatible): any output column can be matched
    # exactly via a query param, e.g. ?status=new&is_mounted=1&actor=foo@bar.com.
    # No such params => same behavior as before. company_id/limit keep their special
    # handling and the soft-delete filter can't be overridden.
    filterable = {c.strip() for c in cfg["columns"].replace("\n", " ").split(",")}
    reserved = {"company_id", "limit", "is_deleted", "created_at", "updated_at"}
    for k, v in qs.items():
        if k in reserved or k not in filterable or v in (None, ""):
            continue
        filters[k] = v

    table = t(resource) if cfg["prefixed"] else resource
    db = get_db()
    try:
        rows = get_many(db, table, cfg["columns"], filters, limit=limit)
        return ok(rows)
    except Exception as e:
        return error(500, f"DB error (list {resource}): {e}")
