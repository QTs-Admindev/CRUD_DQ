"""Vigía de la plataforma (servicio aparte).

Cada 5 minutos revisa que los servicios respondan y, si uno se cae, manda por
WhatsApp la plantilla `platform_incident_detected`. No comparte código, base ni
despliegue con nada más: solo hace peticiones de lectura a URLs públicas y guarda
su estado en su propia tabla de DynamoDB. Si este servicio falla, nada más se
entera; si falla cualquier otro, este avisa.

Un servicio se da por caído con FALLAS_PARA_AVISAR revisiones fallidas seguidas
(un parpadeo no despierta a nadie) y el aviso se repite cada REPETIR_CADA_MS
mientras siga caído.

Plantilla (8 variables): {{1}} servicio caído · {{2}} error · {{3}} detalle ·
{{4}}..{{8}} estado de los otros cinco servicios.

Solo biblioteca estándar más boto3 (ya viene en Lambda): nada que instalar.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

log = logging.getLogger()
log.setLevel(logging.INFO)

CDMX = timezone(timedelta(hours=-6))
TIMEOUT_S = 15
FALLAS_PARA_AVISAR = 2
REPETIR_CADA_MS = 6 * 3600 * 1000

SERVICIOS = [
    ("API de activos (CRUD_DQ)",
     "https://a3083pk5d4.execute-api.us-east-1.amazonaws.com/dev/list/unit_catalog?limit=1"),
    ("API de llantas y conductores (CRUDTires)",
     "https://9ha7iwdou7.execute-api.us-west-1.amazonaws.com/dev/api/companies/catalog"),
    ("Tiempo real (GPSHook)", "https://iot.quinta.tech/init/alerts?cid=136"),
    ("Plataforma de clientes (tms)", "https://tms.quinta.tech/"),
    ("Dashboard (dev)", "https://dev.quinta.tech/"),
    ("Admin", "https://admin.quinta.tech/"),
    # Responde 503 si el espejo está trabado o atrasado; sin respuesta si se cayó.
    ("Espejo de Mongo (análisis)", "https://dev-iot.quinta.tech/espejo/salud"),
]


def cinco(lineas: list[str]) -> list[str]:
    """La plantilla tiene cinco renglones de estado: lo que sobre va junto en el último."""
    return lineas[:4] + [" · ".join(lineas[4:])] if len(lineas) > 5 else lineas


def config() -> dict:
    # `or`: el workflow manda las variables vacías cuando no están definidas en GitHub.
    return {
        "enabled": (os.getenv("INCIDENT_NOTIFY_ENABLED") or "false").lower() == "true",
        "template": os.getenv("WHATSAPP_INCIDENT_TEMPLATE") or "platform_incident_detected",
        # Sin variables. El nombre va así en Meta ("plataform").
        "ok_template": os.getenv("WHATSAPP_OK_TEMPLATE") or "plataform_ok_confirmation",
        "lang": os.getenv("WHATSAPP_INCIDENT_LANG") or "en_US",
        "token": os.getenv("WHATSAPP_TOKEN") or "",
        "phone_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "",
        "api_version": os.getenv("WHATSAPP_API_VERSION") or "v23.0",
        "recipients": [x for x in (os.getenv("INCIDENT_WHATSAPP_RECIPIENTS") or "")
                       .replace(",", " ").split() if x],
        "table": os.getenv("STATE_TABLE") or "",
    }


# ─── Revisión ──────────────────────────────────────────────────────────────────

def revisar(url: str) -> str | None:
    """None si responde bien; si no, el error en pocas palabras."""
    req = urllib.request.Request(url, headers={"User-Agent": "quinta-platform-watcher"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return None if r.status < 400 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except TimeoutError:
        return f"Sin respuesta en {TIMEOUT_S} s"
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            return f"Sin respuesta en {TIMEOUT_S} s"
        return "Error de conexión"
    except Exception as e:
        return f"Error de conexión ({type(e).__name__})"


def variables(caido: str, error: str, url: str, desde_ms: int, fallas: int,
              estados: dict) -> list[str]:
    """Las 8 variables de la plantilla. Meta no acepta variables vacías."""
    host = urllib.parse.urlparse(url).hostname or url
    desde = datetime.fromtimestamp(desde_ms / 1000, CDMX).strftime("%d/%m %H:%M")
    detalle = f"{host} · caído desde {desde} (CDMX) · {fallas} revisiones seguidas"
    otros = [f"{nombre}: {'caído' if err else 'OK'}"
             for nombre, err in estados.items() if nombre != caido]
    return [caido, error or "—", detalle, *cinco(otros)]


# ─── WhatsApp ──────────────────────────────────────────────────────────────────

def enviar(cfg: dict, vars_: list[str], plantilla: str | None = None) -> int:
    """Manda la plantilla a cada destinatario. Devuelve cuántos salieron. No propaga."""
    if not cfg["enabled"]:
        return 0
    if not (cfg["token"] and cfg["phone_id"] and cfg["recipients"]):
        log.error("vigía encendido pero sin token, phone_id o destinatarios")
        return 0
    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/messages"
    enviados = 0
    for numero in cfg["recipients"]:
        cuerpo = json.dumps({
            "messaging_product": "whatsapp",
            "to": str(numero),
            "type": "template",
            "template": {"name": plantilla or cfg["template"], "language": {"code": cfg["lang"]},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": str(v)} for v in vars_]}] if vars_ else []},
        }).encode()
        req = urllib.request.Request(url, data=cuerpo, method="POST", headers={
            "Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S):
                enviados += 1
        except urllib.error.HTTPError as e:
            # El cuerpo de Meta dice qué no le gustó (plantilla, idioma, variables).
            log.error("whatsapp rechazado (%s): %s", e.code, e.read()[:500])
        except Exception as e:
            log.error("whatsapp falló: %s", e)
    return enviados


# ─── Estado (DynamoDB propia) ─────────────────────────────────────────────────

class Estado:
    def __init__(self, tabla: str):
        import boto3
        self.tabla = boto3.resource("dynamodb").Table(tabla)

    def leer(self) -> dict:
        filas = self.tabla.scan().get("Items", [])
        # DynamoDB devuelve los números como Decimal.
        return {f["service"]: {k: (int(v) if isinstance(v, Decimal) else v)
                               for k, v in f.items()} for f in filas}

    def guardar(self, servicio: str, fila: dict) -> None:
        item = {"service": servicio, **{k: v for k, v in fila.items()
                                        if v is not None and k != "service"}}
        self.tabla.put_item(Item=item)


# ─── Ciclo ─────────────────────────────────────────────────────────────────────

def ciclo(estados: dict, previo: dict, ahora: int, cfg: dict) -> tuple[dict, int]:
    """Aplica las reglas de aviso. Devuelve el estado nuevo por servicio y cuántos avisos salieron."""
    urls = dict(SERVICIOS)
    nuevo, avisos = {}, 0
    for nombre, error in estados.items():
        fila = dict(previo.get(nombre) or {})
        if error is None:
            fila = {"fallas": 0}
        else:
            fila["fallas"] = int(fila.get("fallas") or 0) + 1
            fila["caido_desde"] = fila.get("caido_desde") or ahora
            fila["ultimo_error"] = error
            ultimo = fila.get("ultimo_aviso")
            toca = fila["fallas"] >= FALLAS_PARA_AVISAR and (
                not ultimo or ahora - int(ultimo) >= REPETIR_CADA_MS)
            if toca and enviar(cfg, variables(nombre, error, urls[nombre],
                                              int(fila["caido_desde"]), fila["fallas"], estados)):
                fila["ultimo_aviso"] = ahora
                avisos += 1
        fila["updated_at"] = ahora
        nuevo[nombre] = fila
    return nuevo, avisos


def check(event, context, estado_cls=Estado):
    cfg = config()
    ahora = int(time.time() * 1000)
    estados = {nombre: revisar(url) for nombre, url in SERVICIOS}

    # Prueba manual ({"prueba": true}): manda la plantilla una vez con el estado
    # real de los servicios, sin tocar el estado guardado.
    if (event or {}).get("prueba"):
        nombre, url = SERVICIOS[0]
        enviados = enviar(cfg, variables("Prueba de alerta", estados[nombre] or "Sin error",
                                         url, ahora, 0, estados))
        log.info("prueba: enviados=%s estados=%s", enviados, estados)
        return {"prueba": True, "enviados": enviados, "estados": estados}

    estado = estado_cls(cfg["table"])
    try:
        previo = estado.leer()
    except Exception as e:
        log.error("no se pudo leer el estado: %s", e)
        previo = {}
    nuevo, avisos = ciclo(estados, previo, ahora, cfg)
    for nombre, fila in nuevo.items():
        try:
            estado.guardar(nombre, fila)
        except Exception as e:
            log.error("no se pudo guardar el estado de %s: %s", nombre, e)
    caidos = [n for n, e in estados.items() if e]
    log.info("caídos=%s avisos=%s", caidos, avisos)
    return {"caidos": caidos, "avisos": avisos}


# ─── Reporte diario ────────────────────────────────────────────────────────────

def variables_reporte(estados: dict, ahora_ms: int) -> list[str]:
    """Las 8 variables de la plantilla de incidentes con el estado de todos los
    servicios. El primero va en el detalle porque solo hay cinco renglones."""
    hora = datetime.fromtimestamp(ahora_ms / 1000, CDMX).strftime("%d/%m %H:%M")
    caidos = [n for n, e in estados.items() if e]
    resumen = "Todo en línea" if not caidos else f"{len(caidos)} caído(s): {', '.join(caidos)}"
    lineas = [f"{nombre}: {err or 'OK'}" for nombre, err in estados.items()]
    return [f"Reporte diario {hora} (CDMX)", resumen, lineas[0], *cinco(lineas[1:])]


def reporte(event, context):
    cfg = config()
    estados = {nombre: revisar(url) for nombre, url in SERVICIOS}
    # Todo bien: la confirmación corta. Algo caído: el detalle con la de incidentes.
    if any(estados.values()):
        enviados = enviar(cfg, variables_reporte(estados, int(time.time() * 1000)))
    else:
        enviados = enviar(cfg, [], cfg["ok_template"])
    log.info("reporte: enviados=%s estados=%s", enviados, estados)
    return {"enviados": enviados, "estados": estados}
