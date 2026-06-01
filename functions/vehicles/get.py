from shared.db.connection import get_db
from shared.db.ops import get_by_id
from shared.utils.response import error, ok


def handler(event, context):
    try:
        vehicle_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    db = get_db()
    vehicle = get_by_id(db, "vehicles", vehicle_id)
    if not vehicle:
        return error(404, "Vehículo no encontrado")
    return ok(vehicle)
