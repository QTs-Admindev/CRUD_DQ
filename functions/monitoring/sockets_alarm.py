"""Alarmas de CloudWatch del monitor de GPSHook (vía SNS) -> aviso por WhatsApp.

Un mensaje cuando la alarma entra (ALARM) y otro cuando se recupera (OK). Las
transiciones desde o hacia INSUFFICIENT_DATA se ignoran: pasan al crear la alarma
y no dicen nada útil.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from shared.whatsapp import avisar

_log = logging.getLogger(__name__)

CDMX = timezone(timedelta(hours=-6))


def _hora(iso: str | None) -> str:
    try:
        ts = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        ts = datetime.now(timezone.utc)
    return ts.astimezone(CDMX).strftime("%d/%m/%Y %H:%M (CDMX)")


def mensaje(alarma: dict) -> dict | None:
    nuevo, previo = alarma.get("NewStateValue"), alarma.get("OldStateValue")
    if nuevo == "ALARM":
        tipo = "CRÍTICA — Sockets / GPSHook"
    elif nuevo == "OK" and previo == "ALARM":
        tipo = "RECUPERADO — Sockets / GPSHook"
    else:
        return None
    return {
        "tipo": tipo,
        "datos": alarma.get("AlarmDescription") or alarma.get("AlarmName") or "alarma sin nombre",
        "detalle": (alarma.get("NewStateReason") or "sin detalle")[:180],
        "cuando": _hora(alarma.get("StateChangeTime")),
    }


def handler(event, context):
    enviados = 0
    for rec in event.get("Records") or []:
        try:
            alarma = json.loads(rec["Sns"]["Message"])
        except (KeyError, TypeError, ValueError):
            _log.warning("mensaje de SNS que no es una alarma: %s", str(rec)[:300])
            continue
        m = mensaje(alarma)
        if m:
            enviados += avisar(**m)
    return {"sent": enviados}
