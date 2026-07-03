import json

# CORS: en proxy integration el header debe venir del handler, no del gateway.
_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


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
    """Activo creado localmente pero aún no confirmado en Dajin (status registering).

    No es un error: el barrido de reconciliación completará la sincronización.
    """
    return {
        "statusCode": 202,
        "headers": _HEADERS,
        "body": json.dumps(
            {
                "status": "registering",
                "data": data,
                "message": "Activo creado, sincronización con Dajin pendiente",
            },
            default=str,
        ),
    }


def pending_delete(data, reason=None) -> dict:
    """Borrado local hecho, pero la limpieza en Dajin quedó pendiente (Dajin no respondió).

    No es un error: el activo ya no aparece localmente (is_deleted=1) y el barrido de
    reconciliación completará el borrado remoto cuando Dajin vuelva.
    """
    return {
        "statusCode": 202,
        "headers": _HEADERS,
        "body": json.dumps(
            {
                "status": "deleting",
                "data": data,
                "message": "Borrado local hecho, limpieza en Dajin pendiente",
                "reason": reason,
            },
            default=str,
        ),
    }
