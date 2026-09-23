from shared.db.connection import get_db
from shared.db.ops import get_by_id
from shared.utils.response import error, ok
from functions.companies.recipients import public_view


def handler(event, context):
    # GET /companies/{id} — destinatarios de alertas de la compañía (WhatsApp + correo)
    # y sus switches, para poblar el editor del FE. Endpoint acotado a estos campos
    # (no vuelca el resto de `companies`), a diferencia de /list/companies que solo
    # devuelve id/company_name/finance_name para el dropdown.
    try:
        company_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de compañía inválido")

    db = get_db()
    company = get_by_id(db, "companies", company_id)
    if not company:
        return error(404, "Compañía no encontrada")
    return ok(public_view(company))
