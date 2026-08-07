import json
import logging

_log = logging.getLogger("crud_dq")

# CORS: en proxy integration el header debe venir del handler, no del gateway.
_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

# Mensaje único y amable para fallos de sincronización con la plataforma. El usuario NO
# necesita ver el error crudo del proveedor/BD — ese detalle va SOLO al log (CloudWatch).
SYNC_ERROR = "Error de sincronización, intenta de nuevo. Si persiste, contacta a soporte."


def sync_fail(detail="", status=500):
    """Fallo de sync: devuelve un error amable al usuario y manda el detalle real al log.

    Úsalo en los `except` de creación/vinculación/borrado en vez de exponer str(e).
    """
    try:
        _log.warning("sync-fail: %s", detail)
    except Exception:
        pass
    return error(status, SYNC_ERROR)


def ok(data) -> dict:
    return {
        "statusCode": 200,
        "headers": _HEADERS,
        "body": json.dumps(data, default=str),
    }


def error(status_code: int, message) -> dict:
    return {
        "statusCode": status_code,
        "headers": _HEADERS,
        "body": json.dumps({"error": message}, default=str),
    }


def pending(data) -> dict:
    """Activo creado localmente pero aún no confirmado en la plataforma (status registering).

    No es un error: el barrido de reconciliación completará la sincronización.
    """
    return {
        "statusCode": 202,
        "headers": _HEADERS,
        "body": json.dumps(
            {
                "status": "registering",
                "data": data,
                "message": "Creado (en proceso)",
            },
            default=str,
        ),
    }


def pending_delete(data, reason=None) -> dict:
    """Borrado local hecho, pero la limpieza en la plataforma quedó pendiente (la plataforma no respondió).

    No es un error: el activo ya no aparece localmente (is_deleted=1) y el barrido de
    reconciliación completará el borrado remoto cuando la plataforma vuelva.
    """
    return {
        "statusCode": 202,
        "headers": _HEADERS,
        "body": json.dumps(
            {
                "status": "deleting",
                "data": data,
                "message": "Borrado en proceso",
                "reason": reason,
            },
            default=str,
        ),
    }
