"""
test_companies_alerts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Endpoints de destinatarios de alertas por compañía:
  - PUT /companies/{id}  -> functions/companies/update.py
  - GET /companies/{id}  -> functions/companies/get.py

Cubre la normalización de números (E.164 solo-dígitos, quita +/espacios/guiones,
dedup), la validación, el update parcial, y que la forma pública entregue los JSON
ya parseados. `companies` es tabla real (sin prefijo).
"""
import json

from functions.companies import get as cget
from functions.companies import update as cupd


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.updated = []            # (table, id, data) que recibió update()

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def update(self, db, table, rid, data):
        self.updated.append((table, rid, data))
        self.rows[rid].update(data)
        return dict(self.rows[rid])


def _wire(mp, mod, store, *, with_update=False):
    mp.setattr(mod, "get_db", lambda: FakeDB())
    mp.setattr(mod, "get_by_id", store.get_by_id)
    if with_update:
        mp.setattr(mod, "update", store.update)
        mp.setattr(mod, "audit", lambda *a, **k: None)  # best-effort, silenciado


def _put(rid, body):
    return {"pathParameters": {"id": str(rid)}, "body": json.dumps(body)}


def _company(**over):
    base = {"id": 5, "company_name": "Acme", "alert_whatsapp": None,
            "whatsapp_alerts_enabled": 0, "alert_emails": None, "email_alerts_enabled": 0}
    base.update(over)
    return base


# ─── PUT: WhatsApp ─────────────────────────────────────────────────────────────

def test_set_whatsapp_normaliza_y_deduplica(monkeypatch):
    store = Store({5: _company()})
    _wire(monkeypatch, cupd, store, with_update=True)
    # mismo número escrito de dos formas (con +/espacios/guiones y plano) -> 1 solo
    resp = cupd.handler(_put(5, {
        "alert_whatsapp": ["+52 155 1234-5678", "5215512345678", "  "],
        "whatsapp_alerts_enabled": True,
    }), None)
    assert resp["statusCode"] == 200
    # se guardó como JSON de solo-dígitos, deduplicado
    _table, _id, data = store.updated[0]
    assert json.loads(data["alert_whatsapp"]) == ["5215512345678"]
    assert data["whatsapp_alerts_enabled"] == 1
    # la respuesta entrega el JSON ya parseado + flag como bool
    body = json.loads(resp["body"])
    assert body["alert_whatsapp"] == ["5215512345678"]
    assert body["whatsapp_alerts_enabled"] is True


def test_numero_invalido_devuelve_422_y_no_escribe(monkeypatch):
    store = Store({5: _company()})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {"alert_whatsapp": ["123"]}), None)  # muy corto
    assert resp["statusCode"] == 422
    assert store.updated == []


def test_lista_vacia_limpia_los_numeros(monkeypatch):
    store = Store({5: _company(alert_whatsapp='["5215512345678"]')})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {"alert_whatsapp": []}), None)
    assert resp["statusCode"] == 200
    _t, _i, data = store.updated[0]
    assert json.loads(data["alert_whatsapp"]) == []


# ─── PUT: correos + flags ──────────────────────────────────────────────────────

def test_set_emails_valida_y_baja_a_minusculas(monkeypatch):
    store = Store({5: _company()})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {
        "alert_emails": ["Ops@Acme.com", "ops@acme.com"], "email_alerts_enabled": True,
    }), None)
    assert resp["statusCode"] == 200
    _t, _i, data = store.updated[0]
    assert json.loads(data["alert_emails"]) == ["ops@acme.com"]  # dedup + lowercase
    assert data["email_alerts_enabled"] == 1


def test_correo_invalido_devuelve_422(monkeypatch):
    store = Store({5: _company()})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {"alert_emails": ["no-es-correo"]}), None)
    assert resp["statusCode"] == 422
    assert store.updated == []


def test_solo_flag_sin_tocar_listas(monkeypatch):
    store = Store({5: _company(alert_whatsapp='["5215512345678"]')})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {"whatsapp_alerts_enabled": False}), None)
    assert resp["statusCode"] == 200
    _t, _i, data = store.updated[0]
    assert data["whatsapp_alerts_enabled"] == 0
    assert "alert_whatsapp" not in data              # la lista no se tocó


# ─── PUT: bordes ───────────────────────────────────────────────────────────────

def test_body_vacio_devuelve_estado_actual_sin_escribir(monkeypatch):
    store = Store({5: _company(alert_whatsapp='["5215512345678"]', whatsapp_alerts_enabled=1)})
    _wire(monkeypatch, cupd, store, with_update=True)
    resp = cupd.handler(_put(5, {}), None)
    assert resp["statusCode"] == 200
    assert store.updated == []                       # no hubo cambios -> no UPDATE
    body = json.loads(resp["body"])
    assert body["alert_whatsapp"] == ["5215512345678"]
    assert body["whatsapp_alerts_enabled"] is True


def test_company_inexistente_404(monkeypatch):
    store = Store({})
    _wire(monkeypatch, cupd, store, with_update=True)
    assert cupd.handler(_put(99, {"alert_whatsapp": []}), None)["statusCode"] == 404


def test_id_invalido_400(monkeypatch):
    store = Store({})
    _wire(monkeypatch, cupd, store, with_update=True)
    ev = {"pathParameters": {"id": "abc"}, "body": "{}"}
    assert cupd.handler(ev, None)["statusCode"] == 400


# ─── GET ───────────────────────────────────────────────────────────────────────

def test_get_devuelve_forma_publica_parseada(monkeypatch):
    store = Store({5: _company(alert_whatsapp='["5215512345678"]',
                               whatsapp_alerts_enabled=1,
                               alert_emails='["ops@acme.com"]')})
    _wire(monkeypatch, cget, store)
    resp = cget.handler({"pathParameters": {"id": "5"}}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {
        "id": 5, "company_name": "Acme",
        "alert_whatsapp": ["5215512345678"], "whatsapp_alerts_enabled": True,
        "alert_emails": ["ops@acme.com"], "email_alerts_enabled": False,
    }


def test_get_company_inexistente_404(monkeypatch):
    store = Store({})
    _wire(monkeypatch, cget, store)
    assert cget.handler({"pathParameters": {"id": "99"}}, None)["statusCode"] == 404
