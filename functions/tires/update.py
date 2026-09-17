import json

from pydantic import BaseModel, ValidationError

from shared.activation import MARCA_BORRADO
from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.lock import asset_lock
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class UpdateTireRequest(BaseModel):
    prefix: str | None = None
    folio: str | None = None


def handler(event, context):
    # PUT /tires/{id} — edita el prefijo y el folio, que son locales.
    # No hay llamada a la plataforma a propósito: allá la llanta se identifica por
    # `tyreCode`, que es el id local, no el folio. Cambiar el folio no la mueve de
    # sitio en la plataforma y no hay nada que propagar.
    try:
        tire_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    try:
        body = UpdateTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    tire = get_by_id(db, t("tires"), tire_id)
    # Una fila borrada es una línea cerrada: no se edita (mismo criterio que los
    # listados y el delete, que ya la tratan como inexistente).
    if not tire or tire.get("is_deleted"):
        return error(404, "Llanta no encontrada")

    cambios = {k: v for k, v in body.model_dump().items() if v is not None}

    for campo, valor in list(cambios.items()):
        # Un campo en blanco no es "déjalo como está" (eso es no mandarlo): sería
        # borrar la mitad de la llave natural y dejar la llanta sin forma de
        # nombrarse.
        if not str(valor).strip():
            return error(422, f"{campo} no puede ir vacío")
        # La marca de borrado es infraestructura del índice UNIQUE, no un folio.
        # Si entrara por aquí, la auditoría cortaría el valor por esa marca y el
        # folio quedaría escrito a medias. Ver liberar_llave_natural.
        if MARCA_BORRADO in str(valor):
            return error(422, f"{campo} no puede contener '{MARCA_BORRADO}'")
        cambios[campo] = str(valor).strip()

    if not cambios:
        return ok(tire)

    # El folio es lo que el usuario lee para identificar la llanta, así que no se
    # puede repetir dentro de la compañía. El prefijo es visual y NO entra en la
    # cuenta: `TSM-5` y `CEC-5` son el mismo folio 5 para quien lo ve.
    #
    # El índice de la tabla es (prefix, folio, company_id), o sea que la base sola
    # deja pasar el repetido si el prefijo difiere. Por eso la regla se aplica aquí
    # y no se delega al índice, igual que hace el alta.
    cambios["updated_at"] = now_ms()

    # El lock cubre de mirar a escribir. Sin él, dos ediciones simultáneas al mismo
    # folio lo ven libre las dos y las dos escriben: el índice no las detiene porque
    # es (prefix, folio, company_id). Es el mismo lock que toma el alta, con la misma
    # llave, así que también serializa un alta contra una edición.
    try:
        with asset_lock(db, f"folio:{tire.get('company_id')}:{cambios.get('folio')}"):
            if "folio" in cambios and cambios["folio"] != tire.get("folio"):
                ocupado = get_where(
                    db, t("tires"),
                    "folio = %s AND company_id = %s AND id <> %s "
                    "AND (is_deleted IS NULL OR is_deleted = 0)",
                    [cambios["folio"], tire.get("company_id"), tire_id], 1)
                if ocupado:
                    return error(409,
                                 f"El folio '{cambios['folio']}' ya está usado en esta compañía")

            record = update(db, t("tires"), tire_id, cambios)
            db.commit()
    except Exception as e:
        db.rollback()
        # (prefix, folio, company_id) es UNIQUE. Un choque es un error del usuario,
        # no del servidor: se contesta como tal y sin devolverle SQL crudo.
        if "Duplicate" in str(e):
            return error(409, "Ya existe una llanta con ese prefijo y folio")
        return error(500, f"DB error: {e}")

    # La bitácora es best-effort: el cambio ya está confirmado y un fallo al
    # registrarlo no puede convertir un éxito real en un 500.
    try:
        audit(db, event, context, action="update", asset_type="tire", asset_id=tire_id,
              natural_key=record.get("folio"), company_id=record.get("company_id"),
              daijin_id=tire.get("daijin_id"), result="success", changes=cambios)
    except Exception:
        pass

    return ok(record)
