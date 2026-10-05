"""Layout de un unit_catalog -> posiciones de llanta del paquete.

Un unit_catalog describe cuántos ejes tiene la unidad (axles_count) y cuántas
llantas hay en cada eje (tires_axle_1..4). De ahí se derivan las posiciones de
montaje, con la MISMA convención que usa el FE (UnitDiagram):

  - axle_index:     1-based (eje 1, 2, ...)
  - wheel_index:    1-based dentro del eje
  - mount_position: contador absoluto 1-based recorriendo eje por eje

El número de sensores de un paquete = número de posiciones (una por llanta).
"""


def tire_slots(catalog: dict) -> list[dict]:
    """Devuelve la lista de posiciones de llanta derivadas del unit_catalog.

    Cada posición es {axle_index, wheel_index, mount_position}. El largo de la
    lista es N = total de llantas de la unidad (los sensores del paquete deben
    coincidir con este N).
    """
    slots: list[dict] = []
    pos = 0
    axles = int(catalog.get("axles_count") or 0)
    for axle in range(1, axles + 1):
        count = int(catalog.get(f"tires_axle_{axle}") or 0)
        for wheel in range(1, count + 1):
            pos += 1
            slots.append({
                "axle_index": axle,
                "wheel_index": wheel,
                "mount_position": pos,
            })
    return slots


# Nombre libre del paquete: solo sirve para identificarlo, no lo usa ninguna
# regla. Se recorta y no puede quedar vacío ni pasar de 255 (la columna).
PACKAGE_NAME_MAX = 255


def clean_package_name(v):
    if v is None:
        return None
    v = str(v).strip()
    if not v:
        raise ValueError("El nombre del paquete no puede quedar vacío")
    if len(v) > PACKAGE_NAME_MAX:
        raise ValueError(f"El nombre del paquete no puede pasar de {PACKAGE_NAME_MAX} caracteres")
    return v
