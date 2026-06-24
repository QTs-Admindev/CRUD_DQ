import json


def ok(data) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(data, default=str),
    }


def error(status_code: int, message) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}, default=str),
    }


def pending(data) -> dict:
    """Activo creado localmente pero aún no confirmado en Dajin (status registering).

    No es un error: el barrido de reconciliación completará la sincronización.
    """
    return {
        "statusCode": 202,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "status": "registering",
                "data": data,
                "message": "Activo creado, sincronización con Dajin pendiente",
            },
            default=str,
        ),
    }
