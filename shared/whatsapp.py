"""
whatsapp.py
~~~~~~~~~~~
Aviso por WhatsApp cuando algo crítico queda a medias y necesita a una persona.

Es el gemelo síncrono del cliente que ya usa GPSHook: misma Cloud API de Meta,
mismo número de origen y **la misma plantilla ya aprobada** (`qbot_alert`), así
que no hace falta dar de alta nada nuevo en Meta. Lo único que cambia es qué se
mete en las cuatro variables.

Por qué plantilla y no texto libre: fuera de la ventana de 24 h, Meta solo
entrega mensajes iniciados por el negocio si van con una plantilla aprobada. Un
aviso de falla siempre cae fuera de esa ventana.

TRES REGLAS QUE NO SE NEGOCIAN
------------------------------
1. Apagado por defecto. Sin `NOTIFY_ENABLED=true` no sale un solo mensaje.
2. Nunca rompe lo que lo llamó. Un fallo al avisar no puede convertir una
   operación buena en un error, ni una mala en otra distinta. Todo va envuelto.
3. Nunca manda datos de más. Van el activo, qué falló, de qué lado y quién lo
   hizo: lo necesario para actuar. Ni tokens, ni ids de la plataforma que no
   sirvan para buscar, ni cuerpos de request.
"""
import logging
import os

import httpx

_log = logging.getLogger(__name__)

# El número de destino NO se escribe en el código: es configuración de operación
# y cambia sin que cambie el software. Mismo nombre de variable que en GPSHook.
CONFIG = {
    "enabled":     os.getenv("NOTIFY_ENABLED", "false").lower() == "true",
    "token":       os.getenv("WHATSAPP_TOKEN"),
    "phone_id":    os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
    "recipients":  [x for x in (os.getenv("WHATSAPP_RECIPIENTS") or
                                os.getenv("WHATSAPP_DEFAULT_RECIPIENT") or "")
                    .replace(",", " ").split() if x],
    "api_version": os.getenv("WHATSAPP_API_VERSION", "v23.0"),
    "image_url":   os.getenv("WHATSAPP_ALERT_IMAGE_URL"),
}

# Plantilla ya registrada y aprobada en Meta. Cuatro variables de texto libre,
# con texto fijo al principio y al final (Meta no acepta que una variable abra o
# cierre el cuerpo). Ver el encabezado de qbot_alerts.py en GPSHook.
TEMPLATE = "qbot_alert"
LANG = "es_MX"

TIMEOUT = 10.0
REINTENTOS = 2


def _post(payload: dict) -> bool:
    """Un envío, con reintento. Devuelve si Meta lo aceptó. No propaga."""
    url = (f"https://graph.facebook.com/{CONFIG['api_version']}"
           f"/{CONFIG['phone_id']}/messages")
    cabeceras = {"Authorization": f"Bearer {CONFIG['token']}",
                 "Content-Type": "application/json"}
    for intento in range(1, REINTENTOS + 1):
        try:
            r = httpx.post(url, json=payload, headers=cabeceras, timeout=TIMEOUT)
            if r.status_code < 300:
                return True
            # 4xx que no sea 429 es culpa del payload o del token: reintentar no
            # cambia nada y solo retrasa al llamador.
            if 400 <= r.status_code < 500 and r.status_code != 429:
                _log.error("whatsapp rechazado (%s) en el intento %s", r.status_code, intento)
                return False
            _log.warning("whatsapp %s en el intento %s", r.status_code, intento)
        except Exception as e:
            _log.warning("whatsapp falló en el intento %s: %s", intento, e)
    return False


def avisar(tipo: str, datos: str, detalle: str, cuando: str) -> int:
    """Manda el aviso a cada destinatario. Devuelve cuántos salieron.

    Los cuatro textos son las variables de la plantilla, en este orden:
        Tipo: {{1}} · Datos: {{2}} · Detalle: {{3}} · Registrado: {{4}}

    Cada destinatario recibe su propio mensaje: que uno falle no cancela al resto.
    """
    if not CONFIG["enabled"]:
        return 0
    if not (CONFIG["token"] and CONFIG["phone_id"] and CONFIG["recipients"]):
        _log.error("whatsapp habilitado pero sin token, phone_id o destinatarios")
        return 0

    componentes = []
    if CONFIG["image_url"]:
        componentes.append({"type": "header", "parameters": [
            {"type": "image", "image": {"link": CONFIG["image_url"]}}]})
    componentes.append({"type": "body", "parameters": [
        {"type": "text", "text": str(x)} for x in (tipo, datos, detalle, cuando)]})

    enviados = 0
    for numero in CONFIG["recipients"]:
        try:
            ok = _post({
                "messaging_product": "whatsapp",
                "to": str(numero),
                "type": "template",
                "template": {"name": TEMPLATE, "language": {"code": LANG},
                             "components": componentes},
            })
        except Exception as e:          # cinturón sobre tirantes: _post ya no propaga
            _log.warning("whatsapp: destinatario descartado por error: %s", e)
            ok = False
        enviados += 1 if ok else 0
    return enviados
