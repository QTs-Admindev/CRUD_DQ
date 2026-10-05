"""Historial de llantas: tabla `tire_events` (migrations/add_tire_events.sql).

Cada cosa que le pasa a una llanta es un renglón; nunca se edita ni se borra.
`tires` sigue siendo el estado actual (con el que se hacen las cuentas) y aquí
queda lo anterior para no perderlo.
"""
import json

from shared.config import t
from shared.db.ops import get_where, insert
from shared.utils.clock import now_ms

# Cómo entró la llanta a Quinta (solo en el evento 'alta').
ORIGENES = ("new", "used", "renewed", "unconfirmed")


def _nz(v):
    """0 o vacío = no capturado (el alta viejo guardaba 0 por omisión)."""
    try:
        return None if v is None or float(v) == 0 else float(v)
    except (TypeError, ValueError):
        return None


def record_event(db, *, tire_id, event_type, company_id=None, actor=None, origin=None,
                 life_number=None, depth_mm=None, mileage_km=None, cost=None,
                 prev_cost=None, prev_depth_mm=None, prev_mileage_km=None,
                 unit_id=None, mount_position=None, details=None, occurred_at=None):
    """Agrega un renglón al historial. NO hace commit: va en la transacción de
    quien lo llama, para que el cambio y su evento queden juntos o ninguno."""
    ts = now_ms()
    return insert(db, t("tire_events"), {
        "tire_id": tire_id,
        "company_id": company_id,
        "event_type": event_type,
        "occurred_at": occurred_at or ts,
        "actor": actor,
        "origin": origin,
        "life_number": life_number,
        "depth_mm": _nz(depth_mm),
        "mileage_km": _nz(mileage_km),
        "cost": _nz(cost),
        "prev_cost": _nz(prev_cost),
        "prev_depth_mm": _nz(prev_depth_mm),
        "prev_mileage_km": _nz(prev_mileage_km),
        "unit_id": unit_id,
        "mount_position": mount_position,
        "details": json.dumps(details, ensure_ascii=False) if details is not None else None,
        "created_at": ts,
    })


def has_event(db, tire_id, event_type) -> bool:
    return bool(get_where(db, t("tire_events"), "tire_id = %s AND event_type = %s",
                          [tire_id, event_type], 1))
