from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import exists, get_by_id, soft_delete, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending_delete
from functions.vehicles.create import _dajin_type


def handler(event, context):
    # DELETE /vehicles/{id} -> Dajin-first: borra en Dajin (basic-api) y luego soft-delete local.
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
    # Guard: llantas montadas requieren desmontaje manual primero.
    if exists(db, t("tires"), {"unit_id": rid, "is_deleted": 0}):
        return error(409, "El vehículo tiene llantas montadas; desmóntalas primero")

    # Si tiene Qbox asignado, se desvincula automáticamente (queda libre) antes de borrar.
    if rec.get("tbox_id"):
        if rec.get("daijin_id"):
            catalog = get_by_id(db, "unit_catalog", rec.get("unit_catalog_id"))
            if not catalog:
                return error(422, "unit_catalog del vehículo no encontrado")
            is_tractor, model_id = _dajin_type(catalog)
            try:
                st = SmartTyreClient()
                st.post("/smartyre/openapi/vehicle/update", {
                    "id": rec["daijin_id"],
                    "isTractor": is_tractor,
                    "licensePlateNumber": str(rid),
                    "axleTypeId": str(catalog.get("d_id") or ""),
                    "modelId": model_id,
                    "orgId": DAJIN_ORG_ID,
                    "tboxId": "",
                })
            except Exception as e:
                return error(502, "No se pudo desvincular el Qbox, intenta de nuevo")
        try:
            rec = update(db, t("units"), rid, {"tbox_id": None, "updated_at": now_ms()})
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (desvincular Qbox): {e}")

    # Dajin-first: intentar el borrado remoto antes de tocar local.
    daijin_id = rec.get("daijin_id")
    if daijin_id:
        status, msg = attempt_delete("vehicle", str(daijin_id))
        if status == GUARD:
            return error(409, "No se pudo completar el borrado")
    else:
        status, msg = DONE, None  # nunca sincronizó -> nada remoto que borrar

    try:
        if status == TRANSIENT:
            # Dajin no respondió: borrado local hecho, limpieza remota pendiente
            # (conserva daijin_id -> la reconciliación lo retoma).
            rec = soft_delete(db, t("units"), rid)
            db.commit()
            return pending_delete(rec, msg)
        # DONE (o sin daijin_id): soft-delete + limpiar daijin_id (marca cerrado).
        rec = update(db, t("units"), rid, {
            "is_deleted": 1, "daijin_id": None, "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (delete vehículo, daijin_id={daijin_id}): {e}")
