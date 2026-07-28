import json

from pydantic import BaseModel, Field, ValidationError

from shared.audit import audit
from shared.config import ADMIN_COMPANY_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, insert, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending

from functions.packages.layout import tire_slots
# Reusamos EXACTO el alta de tbox y sensores (local-first idempotente + sync a
# Dajin vía resolve_or_create + audit). Aquí solo orquestamos y les ponemos el
# package_id encima. Referencias a nivel módulo para poder mockearlas en tests.
from functions.tboxes.create import handler as tbox_create_handler
from functions.sensors.create import handler as sensor_create_handler


class CreatePackageRequest(BaseModel):
    name: str | None = None
    unit_catalog_id: int
    tboxCode: str
    sensorCodes: list[str] = Field(min_length=1)
    # Un paquete SIEMPRE se prepara en la compañía admin (2). El campo es opcional;
    # si viene, se valida que sea exactamente ADMIN_COMPANY_ID.
    company_id: int | None = None


def _record(resp: dict) -> dict:
    """Extrae el registro del cuerpo de una respuesta de create (ok o pending)."""
    data = json.loads(resp["body"])
    # ok() devuelve el registro directo; pending() lo envuelve en {"data": ...}.
    return data.get("data", data) if isinstance(data, dict) else data


def handler(event, context):
    # 1. Validar input
    try:
        body = CreatePackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    # 2. Un paquete solo se prepara en la compañía admin (2).
    company_id = body.company_id if body.company_id is not None else ADMIN_COMPANY_ID
    if company_id != ADMIN_COMPANY_ID:
        return error(422, f"Los paquetes solo se preparan en la compañía admin (id={ADMIN_COMPANY_ID})")

    db = get_db()

    # 3. Leer el unit_catalog y derivar N = número de posiciones de llanta.
    try:
        catalog = get_by_id(db, "unit_catalog", body.unit_catalog_id)
    except Exception as e:
        return error(500, f"DB error (unit_catalog lookup): {e}")
    if not catalog:
        return error(422, f"unit_catalog_id {body.unit_catalog_id} no existe")

    slots = tire_slots(catalog)
    n = len(slots)
    if n == 0:
        return error(422, f"unit_catalog {body.unit_catalog_id} no tiene llantas configuradas")

    # 4. Validar que la cantidad de sensores coincida con N (una por llanta).
    codes = [str(c).strip().upper() for c in body.sensorCodes]
    if len(set(codes)) != len(codes):
        return error(422, "sensorCodes tiene códigos duplicados")
    if len(codes) != n:
        return error(422, f"El unit_catalog requiere {n} sensores; recibí {len(codes)}")

    headers = (event or {}).get("headers") or {}

    # 5. Crear el TBox (reusa tboxes/create: idempotente + sync a Dajin).
    tresp = tbox_create_handler(
        {"body": json.dumps({"tbox_code": body.tboxCode, "company_id": company_id}),
         "headers": headers}, context)
    if tresp["statusCode"] == 202:
        return pending({"stage": "tbox", "reason": _record(tresp)})
    if tresp["statusCode"] != 200:
        return tresp  # 422/409/500 del alta del tbox -> propagar tal cual
    tbox_id = _record(tresp)["id"]

    # 6. Crear los sensores (reusa sensors/create: idempotente + sync a Dajin).
    sensor_ids: list[int] = []
    for code in codes:
        sresp = sensor_create_handler(
            {"body": json.dumps({"sensor_code": code, "company_id": company_id}),
             "headers": headers}, context)
        if sresp["statusCode"] == 202:
            return pending({"stage": "sensor", "sensor_code": code, "reason": _record(sresp)})
        if sresp["statusCode"] != 200:
            return sresp
        sensor_ids.append(_record(sresp)["id"])

    # 7. Idempotencia del paquete: si el tbox ya cuelga de un paquete 'prepared',
    #    reusarlo en vez de crear otro (re-post del mismo paquete).
    try:
        tbox_row = get_by_id(db, t("tboxes"), tbox_id)
        existing_pid = (tbox_row or {}).get("package_id")
        package_id = None
        if existing_pid:
            pkg = get_by_id(db, t("packages"), existing_pid)
            if pkg and pkg.get("status") == "prepared":
                package_id = existing_pid

        # 8. Insertar el paquete (status 'prepared') si no existe aún.
        if package_id is None:
            ts = now_ms()
            pkg = insert(db, t("packages"), {
                "name": body.name or f"Paquete {body.tboxCode}",
                "unit_catalog_id": body.unit_catalog_id,
                "company_id": company_id,
                "unit_id": None,
                "status": "prepared",
                "created_at": ts,
                "updated_at": ts,
            })
            package_id = pkg["id"]
        else:
            pkg = get_by_id(db, t("packages"), package_id)

        # 9. Sellar package_id en los miembros (tbox + sensores). El sensor i-ésimo
        #    (en el orden en que llegaron los códigos = orden de posición del FE)
        #    guarda su mount_position 1-based, para que el assign mapee sensor->
        #    posición de forma determinista y no dependa del orden de id.
        update(db, t("tboxes"), tbox_id, {"package_id": package_id, "updated_at": now_ms()})
        for pos, sid in enumerate(sensor_ids, start=1):
            update(db, t("sensors"), sid,
                   {"package_id": package_id, "mount_position": pos, "updated_at": now_ms()})
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (armar paquete): {e}")

    audit(db, event, context, action="create", asset_type="package", asset_id=package_id,
          natural_key=body.tboxCode, company_id=company_id, result="success",
          payload={"unit_catalog_id": body.unit_catalog_id, "sensors": len(sensor_ids)})

    return ok({
        **pkg,
        "tbox": get_by_id(db, t("tboxes"), tbox_id),
        "sensors": [get_by_id(db, t("sensors"), sid) for sid in sensor_ids],
    })
