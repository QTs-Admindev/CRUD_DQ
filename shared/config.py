import os

# Prefijo opcional de tablas para apuntar a clones de prueba (ej. "test_") sin tocar
# las tablas reales. Vacío en producción. Se controla con la variable de entorno
# TABLE_PREFIX (se setea en serverless.yml por stage).
TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "")


def t(name: str) -> str:
    """Return the table name with the environment prefix applied."""
    return f"{TABLE_PREFIX}{name}"


# Admin/provider company: sees the whole inventory (including unassigned sensors/tboxes).
ADMIN_COMPANY_ID = 2

# orgId de Dajin: SIEMPRE la organización de Quinta (218). Dajin no conoce nuestros
# company_id — toda la flota vive bajo el org de Quinta. NO usar company_id como orgId.
# Configurable por env por si cambiara.
DAJIN_ORG_ID = os.environ.get("DAJIN_ORG_ID", "218")
