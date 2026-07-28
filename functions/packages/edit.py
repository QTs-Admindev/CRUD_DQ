import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending
from shared.utils.validators import validate_hex12

from functions.packages.layout import tire_slots
# Reusamos EXACTO el alta de tbox y sensores (local-first idempotente + sync a la
# plataforma vía resolve_or_create + audit), igual que create.py. Referencias a
# nivel módulo para poder mockearlas en tests.
from functions.tboxes.create import handler as tbox_create_handler
from functions.sensors.create import handler as sensor_create_handler


class EditPackageRequest(BaseModel):
    # Todos opcionales: solo se toca lo que venga en el body.
    name: str | None = None
    tboxCode: str | None = None
    sensorCodes: list[str] | None = None


def _record(resp: dict) -> dict:
    """Extrae el registro del cuerpo de una respuesta de create (ok o pending)."""
    data = json.loads(resp["body"])
    return data.get("data", data) if isinstance(data, dict) else data


def handler(event, context):
    # PUT /packages/{id} -> edita un paquete AÚN preparado (prepared): nombre, tbox
    # y/o el set completo de sensores. Un paquete asignado/retirado ya no se edita.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")
    try:
        body = EditPackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") != "prepared":
        return error(409, f"El paquete no se puede editar (status={pkg.get('status')})")

    company_id = pkg.get("company_id")
    headers = (event or {}).get("headers") or {}

    # 1. Validar el nuevo set de sensores ANTES de tocar nada (misma forma de 422
    #    que create.py): 12-hex en mayúsculas, sin duplicados y con el largo exacto
    #    N = número de posiciones de llanta del unit_catalog del paquete.
    new_codes = None
    if body.sensorCodes is not None:
        catalog = get_by_id(db, "unit_catalog", pkg.get("unit_catalog_id"))
        if not catalog:
            return error(422, f"unit_catalog del paquete {pid} no encontrado")
        n = len(tire_slots(catalog))
        if n == 0:
            return error(422, f"unit_catalog {pkg.get('unit_catalog_id')} no tiene llantas configuradas")
        try:
            new_codes = [validate_hex12(str(c).strip(), "sensor_code") for c in body.sensorCodes]
        except ValueError as e:
            return error(422, str(e))
        if len(set(new_codes)) != len(new_codes):
            return error(422, "sensorCodes tiene códigos duplicados")
        if len(new_codes) != n:
            return error(422, f"El unit_catalog requiere {n} sensores; recibí {len(new_codes)}")

    # Miembros actuales (antes de cualquier cambio).
    current_tbox = None
    tboxes = get_where(db, t("tboxes"), "package_id = %s", [pid], 50)
    if tboxes:
        current_tbox = tboxes[0]
    current_sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)

    # 2. Resolver el nuevo tbox (si cambió el código) reusando el alta de tbox.
    #    Solo orquestamos: create es idempotente + sincroniza; aquí sellamos encima.
    new_tbox_id = None
    if body.tboxCode and (current_tbox is None or current_tbox.get("tboxCode") != body.tboxCode):
        tresp = tbox_create_handler(
            {"body": json.dumps({"tbox_code": body.tboxCode, "company_id": company_id}),
             "headers": headers}, context)
        if tresp["statusCode"] == 202:
            return pending({"stage": "tbox", "reason": _record(tresp)})
        if tresp["statusCode"] != 200:
            return tresp
        new_tbox_id = _record(tresp)["id"]

    # 3. Resolver los sensores del nuevo set (idempotente) reusando el alta de sensor.
    new_sensor_ids: list[int] = []
    if new_codes is not None:
        for code in new_codes:
            sresp = sensor_create_handler(
                {"body": json.dumps({"sensor_code": code, "company_id": company_id}),
                 "headers": headers}, context)
            if sresp["statusCode"] == 202:
                return pending({"stage": "sensor", "sensor_code": code, "reason": _record(sresp)})
            if sresp["statusCode"] != 200:
                return sresp
            new_sensor_ids.append(_record(sresp)["id"])

    # 4. Aplicar los cambios locales (todo en una transacción).
    changes: dict = {}
    try:
        ts = now_ms()
        if body.name is not None:
            update(db, t("packages"), pid, {"name": body.name, "updated_at": ts})
            changes["name"] = body.name

        if new_tbox_id is not None:
            # Desellar el tbox actual y sellar el nuevo en el paquete.
            if current_tbox is not None:
                update(db, t("tboxes"), current_tbox["id"], {"package_id": None, "updated_at": ts})
            update(db, t("tboxes"), new_tbox_id, {"package_id": pid, "updated_at": ts})
            changes["tboxCode"] = body.tboxCode

        if new_codes is not None:
            # Sellar los nuevos sensores con su mount_position 1-based (orden del set).
            for pos, sid in enumerate(new_sensor_ids, start=1):
                update(db, t("sensors"), sid,
                       {"package_id": pid, "mount_position": pos, "updated_at": ts})
            # Desellar los que estaban en el paquete y ya no están en el nuevo set.
            new_set = set(new_codes)
            for s in current_sensors:
                if s.get("sensorCode") not in new_set:
                    update(db, t("sensors"), s["id"],
                           {"package_id": None, "mount_position": None, "updated_at": ts})
            changes["sensorCodes"] = new_codes

        update(db, t("packages"), pid, {"updated_at": ts})
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (editar paquete): {e}")

    audit(db, event, context, action="update", asset_type="package", asset_id=pid,
          natural_key=pkg.get("name"), company_id=company_id, result="success",
          changes=changes)

    pkg = get_by_id(db, t("packages"), pid)
    tboxes = get_where(db, t("tboxes"), "package_id = %s", [pid], 50)
    sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    return ok({
        **pkg,
        "tbox": tboxes[0] if tboxes else None,
        "sensors": sensors,
    })
