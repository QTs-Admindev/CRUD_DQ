import time


def now_ms() -> int:
    """Epoch en milisegundos (formato de los timestamps de la BD `quinta`)."""
    return int(time.time() * 1000)
