import re

HEX12 = re.compile(r'^[0-9A-Fa-f]{12}$')


def validate_hex12(value: str, field_name: str) -> str:
    if not HEX12.match(value):
        raise ValueError(f"{field_name} debe ser 12 caracteres hexadecimales (0-9, A-F)")
    return value.upper()


# Lote (batch_code) de sensores y Qbox: el identificador del envío que escribe quien
# sube. Mismo largo que las columnas sensors.batch_code y tboxes.batch_code.
BATCH_CODE_MAX = 64


def normalize_batch_code(value: str | None) -> str | None:
    """Recorta el lote; vacío cuenta como sin lote. Lanza ValueError si no cabe o si
    trae caracteres de control (un salto de línea pegado desde Excel, por ejemplo)."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) > BATCH_CODE_MAX:
        raise ValueError(f"batch_code admite hasta {BATCH_CODE_MAX} caracteres")
    if any(not ch.isprintable() for ch in value):
        raise ValueError("batch_code no admite caracteres de control")
    return value
