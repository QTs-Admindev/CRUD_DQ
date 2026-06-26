import json

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_many
from shared.utils.response import error, ok

# Whitelist recurso -> columnas que devolvemos (para los selects del tester).
RESOURCES = {
    "units": "id, unit_identifier, company_id, daijin_id, status, tbox_id",
    "tires": ("id, prefix, folio, company_id, daijin_id, status, "
              "unit_id, sensor_id, is_mounted, axle_index, wheel_index"),
    "sensors": "id, sensorCode, company_id, daijin_id, status",
    "tboxes": "id, tboxCode, company_id, daijin_id, status",
}


def handler(event, context):
    resource = (event.get("pathParameters") or {}).get("resource")
    if resource not in RESOURCES:
        return error(404, f"recurso inválido: {resource} (usa {list(RESOURCES)})")

    qs = event.get("queryStringParameters") or {}
    filters = {"is_deleted": 0}  # los soft-deleted no se listan
    if qs.get("company_id"):
        try:
            filters["company_id"] = int(qs["company_id"])
        except ValueError:
            return error(422, "company_id debe ser entero")

    db = get_db()
    try:
        rows = get_many(db, t(resource), RESOURCES[resource], filters, limit=300)
        return ok(rows)
    except Exception as e:
        return error(500, f"DB error (list {resource}): {e}")
