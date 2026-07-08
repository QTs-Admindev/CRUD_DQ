import json

from pydantic import BaseModel, ValidationError

from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_fields, get_by_id, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending


class CreateVehicleRequest(BaseModel):
    unit_identifier: str
    company_id: int
    unit_catalog_id: int
    tbox_id: int | None = None
    tbox_code: str | None = None
    vin: str = ""
    plates: str | None = None
    mileage: int = 0


def _dajin_type(catalog: dict) -> tuple[int, str]:
    """Calcula (isTractor, modelId) para Dajin a partir del unit_catalog (igual que el v1)."""
    name = (catalog.get("name") or "").lower()
    if catalog.get("type") == "trailer":
        return 2, "39"
    if "truck" in name:
        return 1, "40"
    return 0, "32"


def handler(event, context):
    # 1. Validar input
    try:
        body = CreateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    key = {
        "unit_identifier": body.unit_identifier,
        "company_id": body.company_id,
        "unit_catalog_id": body.unit_catalog_id,
    }
    unit_fields = {
        "tbox_id": body.tbox_id,
        "vin": body.vin,
        "plates": body.plates,
        "mileage": body.mileage,
    }
    DUP_MSG = "Ya existe una unidad con ese identificador para esta compañía y tipo."

    # 2. Local-first. Regla de negocio: no se puede dar de alta 2 veces la misma
    #    unidad (misma clave) salvo que la existente esté borrada (is_deleted=1);
    #    en ese caso se re-activa esa misma fila en vez de duplicarla.
    try:
        existing = get_by_fields(db, t("units"), key)
        if existing and not existing.get("is_deleted"):
            if existing.get("daijin_id"):
                return error(409, DUP_MSG)  # alta ya completada -> duplicado
            local_id = existing["id"]        # alta a medias (registering) -> reanudar
        elif existing:                       # existe pero borrada -> re-alta sobre la misma fila
            update(db, t("units"), existing["id"], {
                **unit_fields, "is_deleted": 0, "daijin_id": None,
                "status": "registering", "updated_at": now_ms(),
            })
            db.commit()
            local_id = existing["id"]
        else:
            try:
                rec = insert(db, t("units"), {
                    **key, **unit_fields, "status": "registering", "updated_at": now_ms(),
                })
                db.commit()
                local_id = rec["id"]
            except Exception:
                # Carrera / clave duplicada: otro proceso la creó -> reevaluar la regla.
                db.rollback()
                existing = get_by_fields(db, t("units"), key)
                if not existing:
                    raise
                if not existing.get("is_deleted") and existing.get("daijin_id"):
                    return error(409, DUP_MSG)
                if existing.get("is_deleted"):
                    update(db, t("units"), existing["id"], {
                        **unit_fields, "is_deleted": 0, "daijin_id": None,
                        "status": "registering", "updated_at": now_ms(),
                    })
                    db.commit()
                local_id = existing["id"]
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (insert unit): {e}")

    # 3. Lookup del catálogo (tabla de referencia REAL, sin prefijo test_)
    try:
        catalog = get_by_id(db, "unit_catalog", body.unit_catalog_id)
        if not catalog:
            return error(422, f"unit_catalog_id {body.unit_catalog_id} no existe")
    except Exception as e:
        return error(500, f"DB error (unit_catalog lookup): {e}")

    is_tractor, model_id = _dajin_type(catalog)

    # 4. Sync con Dajin. Natural key = id local (licensePlateNumber) -> assume_new.
    try:
        st = SmartTyreClient()
        payload = {
            "licensePlateNumber": str(local_id),
            "isTractor": is_tractor,
            "modelId": model_id,
            "axleTypeId": str(catalog.get("d_id") or ""),
            "orgId": DAJIN_ORG_ID,  # Dajin siempre espera el org de Quinta (218), no el company_id
        }
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/vehicle/list",
            list_filter={"licensePlateNumber": str(local_id)},
            insert_path="/smartyre/openapi/vehicle/insert",
            insert_payload=payload,
            assume_new=True,
        )
    except SmartTyreNotResolved:
        return pending(get_by_id(db, t("units"), local_id))
    except Exception as e:
        return pending({"id": local_id, "unit_identifier": body.unit_identifier, "reason": str(e)})

    # 5. Activar.
    try:
        rec = update(db, t("units"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate unit, daijin_id={daijin_id}): {e}")

    # 6. Si viene tbox, atarlo en DAJIN (vehicle/update con tboxId = daijin del tbox),
    #    igual que el endpoint bind_tbox. El vínculo local (units.tbox_id) ya quedó en
    #    el insert; esto lo refleja en Dajin. Best-effort: si falla, la unidad ya está
    #    creada y el tbox se puede reasignar con /vehicles/{id}/tbox/bind.
    if body.tbox_id:
        try:
            tbox = get_by_id(db, t("tboxes"), body.tbox_id)
            if tbox and tbox.get("daijin_id"):
                st.post("/smartyre/openapi/vehicle/update", {
                    "id": daijin_id,
                    "isTractor": is_tractor,
                    "licensePlateNumber": str(local_id),
                    "axleTypeId": str(catalog.get("d_id") or ""),
                    "modelId": model_id,
                    "orgId": DAJIN_ORG_ID,  # Dajin siempre espera el org de Quinta (218), no el company_id
                    "tboxId": tbox["daijin_id"],
                })
            else:
                return ok({**rec, "tbox_bind_warning": "el tbox no tiene daijin_id"})
        except Exception as e:
            return ok({**rec, "tbox_bind_warning": f"bind en Dajin falló: {e}"})

    return ok(rec)
