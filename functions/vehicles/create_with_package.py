import json

from pydantic import BaseModel, ConfigDict, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where
from shared.utils.response import error, ok, pending

from functions.packages.layout import tire_slots
# Reusamos EXACTO el alta de unidad y la asignación de paquete. Referencias a
# nivel módulo para poder mockearlas en tests.
from functions.vehicles.create import handler as vehicle_create_handler
from functions.packages.assign import start_assign


class CreateVehicleWithPackageRequest(BaseModel):
    # Sin tbox_id ni tbox_code: la unidad se da de alta con su paquete, nunca con
    # un Qbox o sensores sueltos.
    model_config = ConfigDict(extra="forbid")

    unit_identifier: str
    company_id: int
    unit_catalog_id: int
    package_id: int
    vin: str = ""
    plates: str | None = None
    mileage: int = 0
    # False: solo valida y crea la unidad; el FE captura sus llantas y después
    # asigna el paquete con POST /packages/{id}/assign (reusa lo ya montado).
    assign: bool = True


def _body(resp: dict):
    try:
        return json.loads(resp.get("body") or "{}")
    except (TypeError, ValueError):
        return {}


def handler(event, context):
    # POST /vehicles/with-package — alta de unidad con su paquete.
    # 1. Valida el paquete ANTES de crear nada: listo (prepared), del mismo tipo de
    #    unidad, en la misma compañía y con los sensores completos. Así no queda una
    #    unidad creada con un paquete que no le corresponde.
    # 2. Crea la unidad (vehicles/create) y lanza la asignación del paquete en segundo
    #    plano (202 con assigning=true); el FE sigue el avance en GET /packages/{id}.
    # Si la unidad queda pendiente de sincronizar (202), el paquete no se puede
    # asignar todavía: se avisa y se asigna después desde la tabla.
    try:
        body = CreateVehicleWithPackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    pkg = get_by_id(db, t("packages"), body.package_id)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") != "prepared":
        return error(409, "El paquete ya no está disponible (ya se asignó o se dio de baja)")
    if pkg.get("unit_catalog_id") != body.unit_catalog_id:
        return error(422, "El paquete es de otro tipo de unidad")
    if pkg.get("company_id") != body.company_id:
        return error(422, "El paquete está en otra empresa; muévelo primero a esta empresa")
    catalog = get_by_id(db, "unit_catalog", body.unit_catalog_id)
    if not catalog:
        return error(422, f"unit_catalog_id {body.unit_catalog_id} no existe")
    n = len(tire_slots(catalog))
    sensores = get_where(db, t("sensors"), "package_id = %s", [body.package_id], 500)
    if len(sensores) < n:
        return error(422, f"El paquete tiene {len(sensores)} sensores y este tipo de unidad lleva {n}")

    headers = (event or {}).get("headers") or {}
    unit_fields = body.model_dump(exclude={"package_id", "assign"})
    vresp = vehicle_create_handler({"body": json.dumps(unit_fields), "headers": headers}, context)
    if vresp["statusCode"] == 202:
        unidad = _body(vresp)
        unidad = unidad.get("data", unidad) if isinstance(unidad, dict) else unidad
        return pending({"unit": unidad, "assigned": False,
                        "reason": "La unidad quedó pendiente de sincronizar; asígnale el paquete "
                                  "cuando termine"})
    if vresp["statusCode"] != 200:
        return vresp  # 409/422/5xx del alta de la unidad, tal cual
    unidad = _body(vresp)
    if not body.assign:
        return ok({"unit": unidad, "assigned": False, "assigning": False, "deferred": True})

    aresp = start_assign(db, event, body.package_id, unidad.get("id"))
    if aresp["statusCode"] == 202:
        paquete = _body(aresp).get("data")
        return pending({"unit": unidad, "assigned": False,
                        "assigning": (paquete or {}).get("status") == "assigning",
                        "package": paquete,
                        "reason": (paquete or {}).get("assign_error")})
    # La unidad ya existe: no se revierte. Se informa para reintentar la asignación.
    detalle = _body(aresp)
    return pending({"unit": unidad, "assigned": False, "assigning": False,
                    "reason": detalle.get("error") if isinstance(detalle, dict) and detalle.get("error")
                    else "El paquete no se pudo asignar; reintenta desde la tabla",
                    "assign_status": aresp["statusCode"]})
