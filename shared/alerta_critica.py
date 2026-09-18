"""
alerta_critica.py
~~~~~~~~~~~~~~~~~
Qué cuenta como crítico y cómo se cuenta, en una sola pieza.

QUÉ ES CRÍTICO AQUÍ
-------------------
No es «algo devolvió error». Un 502 porque la plataforma tardó no es crítico: el
cron lo termina y nadie tiene que hacer nada. Crítico es **lo que queda a medias
y no se arregla solo**, es decir, lo que necesita que una persona vaya y actúe:

  - un borrado que la plataforma rechaza y ya se intentó todo (queda vivo allá,
    borrado aquí, y el folio no se libera);
  - un activo que estaba dado por sincronizado y allá no existe;
  - el propio barrido que no pudo ni arrancar (sin él nada se cierra).

Avisar de lo que se arregla solo entrena a la gente a ignorar los avisos, que es
la forma más común de quedarse sin monitoreo.

QUÉ LLEVA EL MENSAJE
--------------------
Lo necesario para actuar sin abrir la computadora: qué activo (prefijo, folio,
código, compañía y unidad si está montado), qué falló, **de qué lado** —local o
plataforma—, **quién** lo hizo y cuándo. El id local va siempre, porque es con lo
que se busca en las dos plataformas.
"""
import logging
from datetime import datetime, timezone

from shared.audit import actor_from
from shared.whatsapp import avisar

_log = logging.getLogger(__name__)

# Etiqueta legible por tipo de falla. La clave es la que usa el código.
MOTIVOS = {
    "borrado_rechazado":  "Borrado rechazado por la plataforma",
    "activo_fantasma":    "Activo que la plataforma ya no tiene",
    "barrido_caido":      "El barrido de reconciliación no pudo correr",
    "medio_estado":       "Operación a medias entre local y plataforma",
}

LADOS = {
    "local":      "lado local",
    "plataforma": "lado plataforma",
    "ambos":      "ambos lados",
}


def describir_activo(rec: dict, tipo: str) -> str:
    """Cómo se nombra el activo en el mensaje, con lo que sirve para buscarlo."""
    rec = rec or {}
    partes = [f"{tipo} id {rec.get('id', '—')}"]

    if rec.get("prefix") or rec.get("folio"):
        partes.append(f"folio {rec.get('prefix') or ''}{'-' if rec.get('prefix') else ''}"
                      f"{rec.get('folio') or '—'}")
    for campo, etiqueta in (("sensorCode", "código"), ("tboxCode", "código"),
                            ("unit_identifier", "unidad")):
        if rec.get(campo):
            partes.append(f"{etiqueta} {rec[campo]}")
    if rec.get("company_id"):
        partes.append(f"compañía {rec['company_id']}")
    if rec.get("unit_id"):
        pos = rec.get("mount_position")
        partes.append(f"montado en unidad {rec['unit_id']}"
                      + (f" pos {pos}" if pos not in (None, -1) else ""))
    return " · ".join(partes)


def notificar(*, motivo: str, tipo_activo: str, rec: dict = None, lado: str = "plataforma",
              detalle: str = "", event=None, actor: str = None) -> int:
    """Manda el aviso. Nunca lanza: avisar no puede romper a quien avisa.

    motivo: clave de MOTIVOS · tipo_activo: llanta/sensor/Qbox/unidad
    lado:   dónde quedó el problema · detalle: la razón que dio la plataforma
    """
    try:
        quien = actor or (actor_from(event) if event is not None else "cron")
        cuando = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

        texto_detalle = (detalle or "sin detalle")[:180]
        return avisar(
            tipo=f"CRÍTICA — {MOTIVOS.get(motivo, motivo)}",
            datos=describir_activo(rec, tipo_activo),
            detalle=f"{LADOS.get(lado, lado)} — {texto_detalle} — lo hizo: {quien}",
            cuando=cuando,
        )
    except Exception as e:
        _log.warning("no se pudo notificar (%s): %s", motivo, e)
        return 0


def notificar_resumen_barrido(summary: dict) -> int:
    """Un solo mensaje por corrida del cron, y solo si hay algo que atender.

    Agregado a propósito: si 80 filas fallan, 80 mensajes no informan más que uno
    y sí garantizan que nadie los lea. Como el barrido corre cada 5 minutos, el
    techo es un mensaje por corrida, y en una corrida sana son cero.
    """
    try:
        bloqueados = summary.get("guard_blocked", 0)
        fantasmas = summary.get("phantom_cleared", 0)
        errores = summary.get("errors", 0)
        if not (bloqueados or fantasmas or errores):
            return 0

        piezas = []
        if bloqueados:
            piezas.append(f"{bloqueados} borrado(s) que la plataforma rechaza")
        if fantasmas:
            piezas.append(f"{fantasmas} activo(s) que allá ya no existen")
        if errores:
            piezas.append(f"{errores} error(es) durante el barrido")

        cuando = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        return avisar(
            tipo="CRÍTICA — Reconciliación con pendientes",
            datos=f"Barrido del CRUD · {', '.join(piezas)}",
            detalle=("necesitan revisión manual; el barrido no los puede cerrar solo "
                     "— lo corrió: cron"),
            cuando=cuando,
        )
    except Exception as e:
        _log.warning("no se pudo notificar el resumen del barrido: %s", e)
        return 0
