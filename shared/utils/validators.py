import re

HEX12 = re.compile(r'^[0-9A-Fa-f]{12}$')


def validate_hex12(value: str, field_name: str) -> str:
    if not HEX12.match(value):
        raise ValueError(f"{field_name} debe ser 12 caracteres hexadecimales (0-9, A-F)")
    return value.upper()
