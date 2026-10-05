import json

from pydantic import BaseModel, ConfigDict, ValidationError

from shared.audit import actor_from, audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.lock import asset_lock
from shared.db.ops import get_by_id, update
from shared.generic_tire import is_generic_catalog
from shared.tire_events import record_event
from shared.utils.clock import now_ms
from shared.utils.response import error, ok

from functions.tires.update import (
    MAX_COST, MAX_DEPTH_MM, MAX_RETREADS, MIN_DEPTH_MM, _decimales_ok,
)


class RenewTireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # mm del piso nuevo y lo que costó la renovada.
    current_depth: float
    cost: float


def handler(event, context):
    # POST /tires/{id}/renew — la llanta recibe un piso nuevo: sube una vida.
    # `tires` queda con el estado de la vida nueva (status renewed, vida + 1, mm y
    # costo de la renovada: con ese costo se hacen las cuentas). El evento
    # 'renovacion' guarda la foto de la vida que termina (costo, mm, km) para que
    # no se pierda. Cambio y evento van en la misma transacción.
    # Nada se propaga a la plataforma: ahí la llanta no tiene condición ni costo.
    try:
        tire_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    try:
        body = RenewTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    if not MIN_DEPTH_MM <= body.current_depth <= MAX_DEPTH_MM or not _decimales_ok(body.current_depth, 2):
        return error(422, f"current_depth debe estar entre {MIN_DEPTH_MM} y {MAX_DEPTH_MM} mm "
                          f"con máximo 2 decimales")
    if not 0 < body.cost <= MAX_COST or not _decimales_ok(body.cost, 2):
        return error(422, f"cost debe ser mayor a 0 y hasta {MAX_COST:,}, con máximo 2 decimales")

    db = get_db()
    try:
        # Lock por llanta: dos clics a Renovar no deben subir dos vidas.
        with asset_lock(db, f"tire:{tire_id}"):
            tire = get_by_id(db, t("tires"), tire_id)
            if not tire or tire.get("is_deleted"):
                return error(404, "Llanta no encontrada")
            if tire.get("status") == "discarded" or tire.get("is_discarded"):
                return error(409, "Una llanta descartada no se renueva")
            catalogo = get_by_id(db, "tires_catalog", tire.get("tires_catalog_id"))
            if not catalogo or is_generic_catalog(catalogo):
                return error(422, "Asígnale su catálogo real (marca, modelo, medida) antes de renovarla")

            vida = max(int(tire.get("life_number") or 1), 1)
            if vida >= MAX_RETREADS + 1:
                return error(409, f"La llanta ya tiene el máximo de {MAX_RETREADS} renovadas")
            nueva = vida + 1

            record = update(db, t("tires"), tire_id, {
                "status": "renewed",
                "life_number": nueva,
                "current_depth": body.current_depth,
                "cost": body.cost,
                "updated_at": now_ms(),
            })
            evento = record_event(
                db, tire_id=tire_id, event_type="renovacion", company_id=tire.get("company_id"),
                actor=actor_from(event), life_number=nueva,
                depth_mm=body.current_depth, cost=body.cost,
                mileage_km=tire.get("tire_mileage"),
                prev_cost=tire.get("cost"), prev_depth_mm=tire.get("current_depth"),
                prev_mileage_km=tire.get("tire_mileage"),
                unit_id=tire.get("unit_id"), mount_position=tire.get("mount_position"),
                details={"prev_status": tire.get("status"), "prev_life_number": vida})
            db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (renovar llanta): {e}")

    try:
        audit(db, event, context, action="update", asset_type="tire", asset_id=tire_id,
              natural_key=record.get("folio"), company_id=record.get("company_id"),
              result="success",
              changes={"renovacion": {"life_number": nueva, "current_depth": body.current_depth,
                                      "cost": body.cost}})
    except Exception:
        pass

    return ok({**record, "event": evento})
