import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok
from functions.companies.recipients import (
    normalize_emails, normalize_phones, public_view,
)


class UpdateCompanyAlertsRequest(BaseModel):
    # Todos opcionales -> update parcial: solo se persiste lo que venga en el body.
    alert_whatsapp: list[str] | None = None
    whatsapp_alerts_enabled: bool | None = None
    alert_emails: list[str] | None = None
    email_alerts_enabled: bool | None = None


def handler(event, context):
    # PUT /companies/{id} — administra los DESTINATARIOS de alertas de la compañía
    # (números de WhatsApp y correos) + sus switches. `companies` es una tabla REAL
    # (sin TABLE_PREFIX), igual que en vehicles/update.py. GPSHook lee estos campos
    # al mandar la alerta; aquí solo los escribimos.
    try:
        company_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de compañía inválido")
    try:
        body = UpdateCompanyAlertsRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    company = get_by_id(db, "companies", company_id)
    if not company:
        return error(404, "Compañía no encontrada")

    # Solo los campos provistos (no None) se persisten; el resto conserva su valor.
    # Los JSON se guardan como texto (json.dumps) para la columna JSON de MySQL,
    # espejo de cómo GPSHook lee alert_emails con json.loads.
    changes: dict = {}
    try:
        if body.alert_whatsapp is not None:
            changes["alert_whatsapp"] = json.dumps(normalize_phones(body.alert_whatsapp))
        if body.alert_emails is not None:
            changes["alert_emails"] = json.dumps(normalize_emails(body.alert_emails))
    except ValueError as e:
        return error(422, str(e))
    if body.whatsapp_alerts_enabled is not None:
        changes["whatsapp_alerts_enabled"] = int(body.whatsapp_alerts_enabled)
    if body.email_alerts_enabled is not None:
        changes["email_alerts_enabled"] = int(body.email_alerts_enabled)

    if not changes:
        return ok(public_view(company))  # nada que cambiar -> estado actual

    changes["updated_at"] = now_ms()
    try:
        rec = update(db, "companies", company_id, changes)
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (update company alerts): {e}")

    # Auditoría best-effort: el update ya está commiteado; un fallo aquí NUNCA debe
    # convertir un éxito real en 500.
    try:
        audit(db, event, context, action="update", asset_type="company",
              asset_id=company_id, natural_key=rec.get("company_name"),
              company_id=company_id, result="success",
              changes={k: v for k, v in changes.items() if k != "updated_at"})
    except Exception:
        pass
    return ok(public_view(rec))
