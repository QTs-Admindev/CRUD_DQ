import pytest

from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create


class FakeST:
    """Cliente SmartTyre falso. `existing` = lo que devuelve el GET antes de crear;
    `after` = lo que devuelve tras el POST."""

    def __init__(self, existing=None, after=None, fail=False):
        self._existing = existing or []
        self._after = after or []
        self.fail = fail
        self.created = False
        self.posts = []

    def get(self, path, params):
        if self.fail:
            raise ConnectionError("Dajin down")
        return {"records": self._after if self.created else self._existing}

    def post(self, path, body):
        if self.fail:
            raise ConnectionError("Dajin down")
        self.posts.append((path, body))
        self.created = True
        return "Success"


def _call(st, backoff=()):
    return resolve_or_create(
        st,
        list_path="/list",
        list_filter={"sensorCode": "X"},
        insert_path="/insert",
        insert_payload={"sensorCode": "X"},
        backoff=backoff,
    )


def test_returns_existing_without_creating():
    # Idempotencia: si ya existe en Dajin, NO se vuelve a crear.
    st = FakeST(existing=[{"id": 42}])
    assert _call(st) == 42
    assert st.posts == []


def test_creates_then_resolves():
    st = FakeST(existing=[], after=[{"id": 100}])
    assert _call(st) == 100
    assert len(st.posts) == 1


def test_raises_when_never_resolves():
    st = FakeST(existing=[], after=[])  # se crea pero nunca aparece
    with pytest.raises(SmartTyreNotResolved):
        _call(st, backoff=())
    assert len(st.posts) == 1


def test_assume_new_skips_pre_get():
    # Con assume_new=True NO se hace el GET previo: aunque "exista", se crea igual.
    st = FakeST(existing=[{"id": 5}], after=[{"id": 7}])
    result = resolve_or_create(
        st,
        list_path="/list",
        list_filter={"tyreCode": "29906"},
        insert_path="/insert",
        insert_payload={"tyreCode": "29906"},
        assume_new=True,
        backoff=(),
    )
    assert result == 7  # resolvió el id post-creación, no el "existente"
    assert len(st.posts) == 1
