"""PR E: lock por-activo (GET_LOCK) que serializa operaciones concurrentes sobre el mismo
activo. Best-effort: degrada sin romper si el driver no soporta cursor (dobles de test)."""
from shared.db import lock


class Cur:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.store.append((sql, params))

    def fetchone(self):
        return (1,)


class DBWithCursor:
    def __init__(self):
        self.sqls = []

    def cursor(self):
        return Cur(self.sqls)


def test_asset_lock_acquires_and_releases():
    db = DBWithCursor()
    with lock.asset_lock(db, "unit:1", timeout=5):
        pass
    joined = " ".join(s for s, _ in db.sqls)
    assert "GET_LOCK" in joined and "RELEASE_LOCK" in joined


def test_asset_lock_degrades_without_cursor():
    class NoCur:
        pass
    with lock.asset_lock(NoCur(), "x"):   # no debe romper
        pass


# El decorador toma get_db de handler.__globals__ -> este get_db de módulo.
_DBH = DBWithCursor()


def get_db():
    return _DBH


@lock.with_asset_lock(lambda e: "unit:" + str(e["id"]))
def _sample_handler(event, context):
    return "ok:" + str(event["id"])


def test_decorator_runs_handler_under_lock():
    _DBH.sqls.clear()
    assert _sample_handler({"id": 7}, None) == "ok:7"
    joined = " ".join(s for s, _ in _DBH.sqls)
    assert "GET_LOCK" in joined and "RELEASE_LOCK" in joined
