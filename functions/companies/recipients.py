"""
recipients.py
~~~~~~~~~~~~~
Helpers compartidos por los endpoints de destinatarios de alertas por compañía
(GET/PUT /companies/{id}). Normalización + forma pública de los campos que viven
en la tabla real `companies`:

  - alert_whatsapp          JSON array de números (E.164, guardados SOLO dígitos)
  - whatsapp_alerts_enabled 0/1
  - alert_emails            JSON array de correos
  - email_alerts_enabled    0/1

Espejo de cómo GPSHook ya consume `alert_emails` (lo lee con json.loads). Los
números se guardan SIN el `+` porque GPSHook los coloca verbatim en el campo `to`
de la WhatsApp Cloud API (el `+` es opcional allá y lo omitimos para un formato
único). La validación acepta el `+`/espacios/guiones del FE y los descarta.
"""
import json
import re

# E.164: hasta 15 dígitos. MX móvil = 52 + 1 + 10 = 13; fijo = 52 + 10 = 12.
# Pedimos 10–15 dígitos tras limpiar todo lo que no sea número.
_NON_DIGIT = re.compile(r"\D+")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phones(raw) -> list[str]:
    """Limpia cada número a solo-dígitos (quita +, espacios, guiones, paréntesis),
    deduplica conservando el orden y valida 10–15 dígitos. Lanza ValueError con un
    mensaje amable ante un número inválido."""
    if not isinstance(raw, (list, tuple)):
        raise ValueError("alert_whatsapp debe ser una lista de números")
    out: list[str] = []
    for item in raw:
        digits = _NON_DIGIT.sub("", str(item))
        if not digits:
            continue  # entrada vacía -> se ignora (permite limpiar la lista)
        if not (10 <= len(digits) <= 15):
            raise ValueError(f"número de WhatsApp inválido: {item!r} "
                             "(deben ser 10 a 15 dígitos, formato E.164)")
        if digits not in out:
            out.append(digits)
    return out


def normalize_emails(raw) -> list[str]:
    """Normaliza correos (trim + minúsculas), deduplica y valida formato básico."""
    if not isinstance(raw, (list, tuple)):
        raise ValueError("alert_emails debe ser una lista de correos")
    out: list[str] = []
    for item in raw:
        email = str(item).strip().lower()
        if not email:
            continue
        if not _EMAIL.match(email):
            raise ValueError(f"correo inválido: {item!r}")
        if email not in out:
            out.append(email)
    return out


def _parse_json_list(value) -> list:
    """La columna JSON vuelve como str desde PyMySQL; la entregamos ya parseada al FE."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def public_view(company: dict) -> dict:
    """Forma pública que consumen el editor del FE y GPSHook: solo los campos de
    destinatarios de alertas, con los JSON ya parseados y los flags como bool."""
    return {
        "id": company.get("id"),
        "company_name": company.get("company_name"),
        "alert_whatsapp": _parse_json_list(company.get("alert_whatsapp")),
        "whatsapp_alerts_enabled": bool(company.get("whatsapp_alerts_enabled")),
        "alert_emails": _parse_json_list(company.get("alert_emails")),
        "email_alerts_enabled": bool(company.get("email_alerts_enabled")),
    }
