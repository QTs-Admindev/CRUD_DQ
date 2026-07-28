from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_many
from shared.utils.response import error, ok

DEFAULT_LIMIT = 300
MAX_LIMIT = 5000

COLUMNS = ("id, name, unit_catalog_id, company_id, unit_id, status, "
           "created_at, updated_at")


def handler(event, context):
    # GET /packages -> lista de paquetes. Filtros opcionales por query string:
    #   ?company_id=..  (la compañía admin ve todo), ?status=prepared|assigned|retired
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
        rows = get_many(db, t("packages"), COLUMNS, filters, limit=limit)
        return ok(rows)
    except Exception as e:
        return error(500, f"DB error (list packages): {e}")
