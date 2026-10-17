"""Vigía de la plataforma: revisa cada 5 minutos que los servicios respondan y, si
uno se cae, manda por WhatsApp la plantilla de incidente.

Un servicio se da por caído con FALLAS_PARA_AVISAR revisiones fallidas seguidas
(un parpadeo no despierta a nadie). Mientras siga caído se repite el aviso cada
REPETIR_CADA_MS. El estado vive en `platform_health` (una fila por servicio), así
cada corrida sabe qué pasó en la anterior.

Plantilla (8 variables): {{1}} servicio caído · {{2}} error · {{3}} detalle ·
{{4}}..{{8}} estado de los otros cinco servicios.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pymysql.cursors

from shared.db.connection import get_db
from shared.whatsapp import enviar_plantilla

_log = logging.getLogger(__name__)

CDMX = timezone(timedelta(hours=-6))
TABLA = "platform_health"
TIMEOUT_S = 15
FALLAS_PARA_AVISAR = 2
REPETIR_CADA_MS = 6 * 3600 * 1000

CONFIG = {
    "enabled": os.getenv("INCIDENT_NOTIFY_ENABLED", "false").lower() == "true",
    "template": os.getenv("WHATSAPP_INCIDENT_TEMPLATE", ""),
    # `or`: el workflow manda la variable vacía cuando no está definida en GitHub.
    "lang": os.getenv("WHATSAPP_INCIDENT_LANG") or "en",
    "recipients": [x for x in (os.getenv("INCIDENT_WHATSAPP_RECIPIENTS") or "")
                   .replace(",", " ").split() if x],
}


def servicios() -> list[tuple[str, str]]:
    """Los seis servicios que se vigilan: (nombre que sale en el mensaje, URL)."""
    return [
        ("API de activos (CRUD_DQ)", f"{os.getenv('CRUD_DQ_URL', '')}/list/unit_catalog?limit=1"),
        ("API de llantas y conductores (CRUDTires)",
         os.getenv("CRUDTIRES_HEALTH_URL",
                   "https://9ha7iwdou7.execute-api.us-west-1.amazonaws.com/dev/api/companies/catalog")),
        ("Tiempo real (GPSHook)",
         os.getenv("GPSHOOK_HEALTH_URL", "https://iot.quinta.tech/init/alerts?cid=136")),
        ("Plataforma de clientes (tms)", "https://tms.quinta.tech/"),
        ("Dashboard (dev)", "https://dev.quinta.tech/"),
        ("Admin", "https://admin.quinta.tech/"),
    ]


def revisar(url: str) -> str | None:
    """None si responde bien; si no, el error en pocas palabras."""
    try:
        r = httpx.get(url, timeout=TIMEOUT_S, follow_redirects=True)
    except httpx.TimeoutException:
        return f"Sin respuesta en {TIMEOUT_S} s"
    except Exception as e:
        return f"Error de conexión ({type(e).__name__})"
    if r.status_code >= 400:
        return f"HTTP {r.status_code}"
    return None


def _hora(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CDMX).strftime("%d/%m %H:%M")


def variables(caido: str, error: str, url: str, desde_ms: int, fallas: int,
              estados: dict[str, str | None]) -> list[str]:
    """Las 8 variables de la plantilla. Meta no acepta variables vacías."""
    host = httpx.URL(url).host or url
    detalle = f"{host} · caído desde {_hora(desde_ms)} (CDMX) · {fallas} revisiones seguidas"
    otros = [f"{nombre}: {'caído' if err else 'OK'}"
             for nombre, err in estados.items() if nombre != caido]
    return [caido, error or "—", detalle, *otros][:8]


def _leer_estado(db) -> dict[str, dict]:
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(f"SELECT * FROM {TABLA}")
        return {r["service"]: r for r in cur.fetchall()}


def _guardar(db, servicio: str, fila: dict) -> None:
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLA} (service, fallas, caido_desde, ultimo_aviso, ultimo_error, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE fallas = VALUES(fallas), "
            "caido_desde = VALUES(caido_desde), ultimo_aviso = VALUES(ultimo_aviso), "
            "ultimo_error = VALUES(ultimo_error), updated_at = VALUES(updated_at)",
            [servicio, fila["fallas"], fila["caido_desde"], fila["ultimo_aviso"],
             (fila["ultimo_error"] or None) and fila["ultimo_error"][:255], fila["updated_at"]])


def avisar(vars_: list[str]) -> int:
    if not CONFIG["enabled"]:
        return 0
    if not (CONFIG["template"] and CONFIG["recipients"]):
        _log.error("alerta de plataforma encendida pero sin plantilla o destinatarios")
        return 0
    return enviar_plantilla(CONFIG["template"], CONFIG["lang"], vars_, CONFIG["recipients"])


def handler(event, context):
    ahora = int(time.time() * 1000)
    lista = servicios()
    estados = {nombre: revisar(url) for nombre, url in lista}

    # Prueba manual (invocar con {"prueba": true}): manda la plantilla una vez con
    # el estado real de los servicios, sin tocar la tabla.
    if (event or {}).get("prueba"):
        nombre, url = lista[0]
        enviados = avisar(variables("Prueba de alerta", estados[nombre] or "Sin error",
                                    url, ahora, 0, estados))
        return {"prueba": True, "enviados": enviados, "estados": estados}

    db = get_db()
    try:
        previo = _leer_estado(db)
    except Exception as e:
        _log.error("no se pudo leer %s: %s", TABLA, e)
        return {"error": str(e), "estados": estados}

    avisos = 0
    for nombre, url in lista:
        error = estados[nombre]
        fila = dict(previo.get(nombre) or {"fallas": 0, "caido_desde": None, "ultimo_aviso": None})
        if error is None:
            fila.update(fallas=0, caido_desde=None, ultimo_aviso=None, ultimo_error=None)
        else:
            fila["fallas"] = (fila.get("fallas") or 0) + 1
            fila["caido_desde"] = fila.get("caido_desde") or ahora
            fila["ultimo_error"] = error
            toca = fila["fallas"] >= FALLAS_PARA_AVISAR and (
                not fila.get("ultimo_aviso") or ahora - fila["ultimo_aviso"] >= REPETIR_CADA_MS)
            if toca and avisar(variables(nombre, error, url, fila["caido_desde"],
                                         fila["fallas"], estados)):
                fila["ultimo_aviso"] = ahora
                avisos += 1
        fila["updated_at"] = ahora
        try:
            _guardar(db, nombre, fila)
        except Exception as e:
            _log.error("no se pudo guardar el estado de %s: %s", nombre, e)
    try:
        db.commit()
    except Exception:
        db.rollback()
    caidos = [n for n, e in estados.items() if e]
    if caidos:
        _log.warning("servicios con falla: %s", caidos)
    return {"caidos": caidos, "avisos": avisos}
