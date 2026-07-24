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
