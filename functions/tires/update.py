import json

from pydantic import BaseModel, ConfigDict, ValidationError

from shared.activation import MARCA_BORRADO
from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.lock import asset_lock
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok

# Mismos límites que el formulario del FE (src/utils/tireValidation.ts y
# tireCondition.ts). Si cambian allá, cambian aquí.
MIN_DEPTH_MM, MAX_DEPTH_MM = 3, 30
MAX_MILEAGE_KM = 600_000
MAX_COST = 1_000_000
MAX_RETREADS = 4          # renovada = vida 2 a MAX_RETREADS + 1
CONDICIONES = ("new", "used", "renewed")


class UpdateTireRequest(BaseModel):
    # Un campo que el endpoint no conoce es un error, no algo que se ignora. Antes
    # se descartaba en silencio: el FE mandaba catálogo, condición, costo,
    # profundidad y kilometraje, el endpoint contestaba 200 y nada se guardaba.
    model_config = ConfigDict(extra="forbid")

    prefix: str | None = None
    folio: str | None = None
    # No mueve la llanta de compañía: el FE lo manda siempre y solo se acepta si es
    # la suya. Cambiar de compañía es otra operación.
    company_id: int | None = None
    tires_catalog_id: int | None = None
    status: str | None = None
    life_number: int | None = None
    cost: float | None = None
    current_depth: float | None = None
    tire_mileage: float | None = None


_DATOS = ("tires_catalog_id", "status", "life_number", "cost", "current_depth", "tire_mileage")


def _igual(nuevo, guardado) -> bool:
    """Compara lo que llega contra lo guardado. MySQL devuelve Decimal o texto
    según la columna, así que los números se comparan como números."""
    if guardado is None:
        return False
    try:
        return abs(float(nuevo) - float(guardado)) < 1e-9
    except (TypeError, ValueError):
        return str(nuevo) == str(guardado)


def _decimales_ok(valor: float, decimales: int) -> bool:
    escalado = valor * (10 ** decimales)
    return abs(escalado - round(escalado)) < 1e-6


def _profundidad_de_fabrica(catalogo: dict | None) -> float | None:
    """La profundidad de fábrica solo es límite si es creíble (mismo criterio que
    usableFactoryDepth en el FE: datos malos del catálogo no bloquean)."""
    try:
        valor = float((catalogo or {}).get("max_depth"))
    except (TypeError, ValueError):
        return None
    return valor if MIN_DEPTH_MM < valor <= MAX_DEPTH_MM else None


def _validar_datos(db, tire: dict, cambiados: dict) -> str | None:
    """Valida SOLO lo que cambia. Hay llantas viejas con valores fuera de las
    reglas actuales (profundidad 0 del alta, por ejemplo) y el FE reenvía el valor
    guardado aunque no se toque: validarlo bloquearía editar el folio."""
    catalogo = None
    if "tires_catalog_id" in cambiados:
        catalogo = get_by_id(db, "tires_catalog", cambiados["tires_catalog_id"])
        if not catalogo:
            return "tires_catalog_id no existe"

    if "status" in cambiados or "life_number" in cambiados:
        status = cambiados.get("status", tire.get("status"))
        vida = cambiados.get("life_number", tire.get("life_number"))
        if status not in CONDICIONES:
            return f"status debe ser uno de {', '.join(CONDICIONES)}"
        if status == "renewed":
            if vida is None or not 2 <= int(vida) <= MAX_RETREADS + 1:
                return f"una llanta renovada va de la vida 2 a la {MAX_RETREADS + 1}"
        elif vida is not None and int(vida) != 1:
            return "una llanta nueva o usada es de primera vida (life_number 1)"

    if "cost" in cambiados:
        costo = cambiados["cost"]
        if not 0 <= costo <= MAX_COST or not _decimales_ok(costo, 2):
            return f"cost debe estar entre 0 y {MAX_COST:,} con máximo 2 decimales"

    if "current_depth" in cambiados:
        prof = cambiados["current_depth"]
        if not MIN_DEPTH_MM <= prof <= MAX_DEPTH_MM or not _decimales_ok(prof, 2):
            return (f"current_depth debe estar entre {MIN_DEPTH_MM} y {MAX_DEPTH_MM} mm "
                    f"con máximo 2 decimales")
        if catalogo is None and tire.get("tires_catalog_id"):
            catalogo = get_by_id(db, "tires_catalog", tire["tires_catalog_id"])
        fabrica = _profundidad_de_fabrica(catalogo)
        if fabrica is not None and prof > fabrica:
            return f"current_depth no puede pasar la profundidad de fábrica ({fabrica} mm)"

    if "tire_mileage" in cambiados:
        km = cambiados["tire_mileage"]
        if not 0 <= km <= MAX_MILEAGE_KM or not _decimales_ok(km, 0):
            return f"tire_mileage debe ser un entero entre 0 y {MAX_MILEAGE_KM:,}"

    return None


def handler(event, context):
    # PUT /tires/{id} — edita la llanta: prefijo y folio, y sus datos de negocio
    # (catálogo, condición, costo, profundidad y kilometraje).
    # No hay llamada a la plataforma a propósito: allá la llanta se identifica por
    # `tyreCode`, que es el id local, no el folio, y el alta le manda marca y medida
    # fijas (no hay mapeo de tires_catalog), así que nada de esto se propaga.
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

    pedido = body.model_dump()

    if pedido["company_id"] is not None and pedido["company_id"] != tire.get("company_id"):
        return error(422, "company_id no coincide con la compañía de la llanta; "
                          "esta ruta no cambia la llanta de compañía")

    cambiados = {k: pedido[k] for k in _DATOS
                 if pedido[k] is not None and not _igual(pedido[k], tire.get(k))}
    problema = _validar_datos(db, tire, cambiados)
    if problema:
        return error(422, problema)

    cambios = {k: pedido[k] for k in ("prefix", "folio") if pedido[k] is not None}

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

    cambios.update(cambiados)

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
                    # Decir CUÁL lo tiene, no solo que está ocupado: sin eso el
                    # usuario queda atorado sin saber qué hacer, y la llanta que
                    # estorba puede ser una que ni sabía que existía.
                    otra = ocupado[0]
                    return error(409, f"El folio '{cambios['folio']}' ya lo usa la llanta "
                                      f"{otra.get('prefix') or ''}{'-' if otra.get('prefix') else ''}"
                                      f"{otra.get('folio')} (id {otra.get('id')}) "
                                      f"en esta compañía")

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
