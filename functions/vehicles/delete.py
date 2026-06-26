from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import exists, get_by_id, soft_delete
from shared.utils.response import error, ok


def handler(event, context):
    # DELETE /vehicles/{id} -> soft delete (sets is_deleted=1, keeps the row).
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    db = get_db()
    rec = get_by_id(db, t("units"), rid)
    if not rec:
        return error(404, "Vehículo no encontrado")
    if rec.get("is_deleted"):
        return ok(rec)  # already deleted -> idempotent
    # Cannot delete while it has mounted tires or an assigned tbox; unbind first.
    if rec.get("tbox_id") or exists(db, t("tires"), {"unit_id": rid, "is_deleted": 0}):
        return error(409, "El vehículo tiene llantas o tbox; desvincula primero")

    # TODO Dajin: remote delete contract not confirmed. There is no delete endpoint in the
    # v1/asset-manager references; Dajin tracks isDeleted internally. Confirm via the Dajin
    # OpenAPI section / support before wiring the remote delete.

    try:
        rec = soft_delete(db, t("units"), rid)
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (soft delete vehículo): {e}")
