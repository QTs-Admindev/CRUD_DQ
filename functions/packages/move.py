import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class MovePackageRequest(BaseModel):
    company_id: int


def handler(event, context):
    # POST /packages/{id}/move -> reasigna el paquete (y su tbox + sensores) a otra
    # compañía. Es una operación LOCAL: Dajin usa orgId 218 (no company), así que no
    # hay nada que sincronizar con la plataforma.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")
    try:
        body = MovePackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    # Solo se puede mover un paquete que sigue armado (prepared). Uno ya asignado
    # está montado en una unidad; uno retirado está dado de baja.
    if pkg.get("status") != "prepared":
        return error(409, f"El paquete no se puede mover (status={pkg.get('status')})")

    # Cascada local: package + tbox + sensores toman la nueva compañía.
    try:
        ts = now_ms()
        update(db, t("packages"), pid, {"company_id": body.company_id, "updated_at": ts})
        for tbox in get_where(db, t("tboxes"), "package_id = %s", [pid], 50):
            update(db, t("tboxes"), tbox["id"], {"company_id": body.company_id, "updated_at": ts})
        for sensor in get_where(db, t("sensors"), "package_id = %s", [pid], 500):
            update(db, t("sensors"), sensor["id"], {"company_id": body.company_id, "updated_at": ts})
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (mover paquete): {e}")

    rec = get_by_id(db, t("packages"), pid)
    audit(db, event, context, action="update", asset_type="package", asset_id=pid,
          natural_key=pkg.get("name"), company_id=body.company_id, result="success",
          changes={"company_id": body.company_id})
    return ok(rec)
