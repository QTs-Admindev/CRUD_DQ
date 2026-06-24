import os

# Prefijo opcional de tablas para apuntar a clones de prueba (ej. "test_") sin tocar
# las tablas reales. Vacío en producción. Se controla con la variable de entorno
# TABLE_PREFIX (se setea en serverless.yml por stage).
TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "")


def t(name: str) -> str:
    """Devuelve el nombre de tabla con el prefijo de entorno aplicado."""
    return f"{TABLE_PREFIX}{name}"
