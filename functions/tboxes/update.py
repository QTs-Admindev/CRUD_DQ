import json
import logging

from pydantic import BaseModel, ValidationError

from shared.activation import NotOnPlatform, PlatformUnavailable, confirm_on_platform
from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import SYNC_ERROR, error, ok

_log = logging.getLogger(__name__)

VALID_STATUSES = {"registering", "active", "inactive"}

NOT_ON_PLATFORM_MSG = (
    "El Qbox no está dado de alta en la plataforma; sincronízalo antes de activarlo"
)


class UpdateTboxRequest(BaseModel):
    status: str | None = None
    company_id: int | None = None


def handler(event, context):
    try:
        tbox_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de tbox inválido")

    try:
        body = UpdateTboxRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    if body.status and body.status not in VALID_STATUSES:
        return error(422, f"status debe ser uno de: {', '.join(VALID_STATUSES)}")

    db = get_db()
    tbox = get_by_id(db, t("tboxes"), tbox_id)
    # Una fila borrada es una línea cerrada: no se edita (mismo criterio que los
    # listados y el delete, que ya la tratan como inexistente).
    if not tbox or tbox.get("is_deleted"):
        return error(404, "TBox no encontrado")

    mysql_payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not mysql_payload:
        return ok(tbox)

    # 'active' se CONFIRMA, no se declara: el Qbox tiene que estar dado de alta en la
    # plataforma. Si no, quedaría un activo local que la plataforma no conoce y que
    # además se sale de /tboxes/resync y del cron de reconciliación.
    # Solo se confirma en la TRANSICIÓN a activo (o si la fila está activa sin id, que
    # es justo el estado corrupto que se quiere sanar). Repetir "activo" sobre una fila
    # ya activa y con id no consulta nada: así una edición de compañía no se cae cuando
    # la plataforma está lenta, y el invariante sigue en pie.
    needs_confirmation = (
        mysql_payload.get("status") == "active"
        and (tbox.get("status") != "active" or not tbox.get("daijin_id"))
    )
    # La guarda también aplica al revés: devolver a 'registering' una fila que YA tiene
    # id en la plataforma la deja en un limbo (el barrido no la toca porque tiene id, y
    # el estado de los importes masivos la cuenta como pendiente para siempre).
    if mysql_payload.get("status") == "registering" and tbox.get("daijin_id"):
        return error(422, "El Qbox ya está sincronizado; no se puede marcar como pendiente")

    healed_id = None
    if needs_confirmation:
        try:
            daijin_id = confirm_on_platform(tbox, "tboxes")
        except NotOnPlatform:
            return error(409, NOT_ON_PLATFORM_MSG)
        except PlatformUnavailable as e:
            _log.warning("activación sin confirmar (tbox id=%s): %s", tbox_id, e)
            return error(502, SYNC_ERROR)
        if str(tbox.get("daijin_id") or "") != daijin_id:
            # Autocuración: la plataforma lo tiene con otro id (o nunca se guardó).
            mysql_payload["daijin_id"] = daijin_id
            healed_id = daijin_id

    mysql_payload["updated_at"] = now_ms()

    try:
        record = update(db, t("tboxes"), tbox_id, mysql_payload)
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

    if healed_id:
        # Best-effort: el cambio ya está confirmado, un fallo de bitácora no lo tumba.
        try:
            audit(db, event, context, action="reconcile", asset_type="tbox",
                  asset_id=tbox_id, natural_key=tbox.get("tboxCode"),
                  company_id=record.get("company_id"), daijin_id=healed_id,
                  result="success", changes={"daijin_id": healed_id, "status": "active"})
        except Exception:
            pass

    return ok(record)
