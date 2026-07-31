"""Reintento de sincronización (resync) de Qboxes atascados: POST /tboxes/resync.

El bulk import inserta filas como 'registering' y el worker asíncrono las
sincroniza contra la plataforma. Si tras MAX_PASSES alguna sigue 'registering',
queda atascada sin forma de reintentar desde la app. Este endpoint vuelve a
disparar el worker (misma invocación Event que bulk_create) para esas filas.

Body opcional { "company_id"?: int, "ids"?: [int] }:
  - company_id: admin puede apuntar a una compañía u omitirlo (alcance global);
    un llamador acotado solo reintenta su propia compañía (igual que list_assets).
  - ids: si se dan, se limita a esas filas (que además deben seguir 'registering').

No talks to the platform: solo re-encola. Responde de inmediato.
"""
import json
import os

import boto3
from pydantic import BaseModel, ValidationError

from shared.audit import actor_from
from shared.auth import resolve_company_scope
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_where
from shared.utils.response import error, ok

# Ids por invocación al worker: acota cada corrida para que ninguna sea ilimitada.
CHUNK_SIZE = 300

# Tope de filas atascadas que recogemos por llamada (mismo orden que el bulk máximo).
MAX_ROWS = 5000


class ResyncRequest(BaseModel):
    company_id: int | None = None
    ids: list[int] | None = None


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _invoke_worker(ids: list[int], actor: str) -> bool:
    """Fire-and-forget del worker de sincronización (mismo patrón que _reinvoke).
    Fallar al lanzar NO es fatal: las filas siguen 'registering' y se puede
    volver a llamar a /tboxes/resync."""
    fn = os.environ.get("BULK_SYNC_FUNCTION")
    if not fn or not ids:
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps({"ids": ids, "pass": 1, "actor": actor}).encode(),
        )
        return True
    except Exception:
        return False


def handler(event, context):
    # 1. Validar el body (todo opcional)
    try:
        req = ResyncRequest.model_validate(json.loads(event.get("body") or "{}"))
    except (ValidationError, ValueError) as e:
        return error(422, e.errors() if isinstance(e, ValidationError) else str(e))

    # 2. Alcance por compañía: admin ve todo, el resto solo su compañía (como list_assets).
    scope = resolve_company_scope(event, req.company_id)

    # 3. Seleccionar SOLO las filas atascadas ('registering', no borradas), acotadas
    #    por compañía y/o por ids explícitos cuando se pidan.
    where = "status = %s AND (is_deleted IS NULL OR is_deleted = 0)"
    params: list = ["registering"]
    if scope is not None:
        where += " AND company_id = %s"
        params.append(scope)
    if req.ids:
        placeholders = ", ".join(["%s"] * len(req.ids))
        where += f" AND id IN ({placeholders})"
        params.extend(req.ids)

    db = get_db()
    try:
        rows = get_where(db, t("tboxes"), where, params, limit=MAX_ROWS)
    except Exception as e:
        return error(500, f"DB error (resync tboxes): {e}")

    ids = [r["id"] for r in rows]
    if not ids:
        return ok({"queued": 0, "batches": 0,
                   "message": "No hay Qboxes en 'registering' para reintentar"})

    # 4. Re-disparar el worker en lotes (mismo actor que bulk_create, default 'resync').
    actor = actor_from(event)
    if actor == "system":
        actor = "resync"

    batches = 0
    for chunk in _chunks(ids, CHUNK_SIZE):
        if _invoke_worker(chunk, actor):
            batches += 1

    return ok({
        "queued": len(ids),
        "batches": batches,
        "message": f"Reintento de sincronización encolado para {len(ids)} Qbox(es)",
    })
