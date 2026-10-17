"""Vigía de la plataforma: avisa por WhatsApp cuando un servicio se cae.

Se protege: que un parpadeo no despierte a nadie (hacen falta 2 fallas seguidas),
que no se repita el aviso en cada corrida, que al volver se reinicie, y que el
mensaje lleve las 8 variables de la plantilla.
"""
import sqlite3

import pymysql
import pytest

from functions.monitoring import plataforma as pf


class _Cursor:
    def __init__(self, conn, como_dict):
        self._cur, self._dict = conn.cursor(), como_dict

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._cur.close()

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        if "ON DUPLICATE KEY UPDATE" in sql:
            sql = sql.split(" ON DUPLICATE KEY UPDATE")[0].replace("INSERT INTO", "INSERT OR REPLACE INTO")
        self._cur.execute(sql, list(params))

    def fetchall(self):
        filas = self._cur.fetchall()
        if not self._dict:
            return filas
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, f)) for f in filas]


class DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE platform_health (service TEXT PRIMARY KEY, fallas INT, "
                          "caido_desde INT, ultimo_aviso INT, ultimo_error TEXT, updated_at INT)")

    def cursor(self, clase=None):
        return _Cursor(self.conn, clase is pymysql.cursors.DictCursor)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


@pytest.fixture
def entorno(monkeypatch):
    db, enviados, estado = DB(), [], {}
    monkeypatch.setattr(pf, "get_db", lambda: db)
    monkeypatch.setattr(pf, "revisar", lambda url: estado.get(url))
    monkeypatch.setattr(pf, "enviar_plantilla",
                        lambda plantilla, idioma, vars_, dest: enviados.append((plantilla, idioma, vars_)) or len(dest))
    monkeypatch.setitem(pf.CONFIG, "enabled", True)
    monkeypatch.setitem(pf.CONFIG, "template", "platform_incident")
    monkeypatch.setitem(pf.CONFIG, "recipients", ["5210000000000"])
    monkeypatch.setenv("CRUD_DQ_URL", "https://api.test/dev")
    reloj = {"ms": 1_800_000_000_000}
    monkeypatch.setattr(pf.time, "time", lambda: reloj["ms"] / 1000)
    return db, enviados, estado, reloj


def _url(nombre):
    return dict(pf.servicios())[nombre]


def test_un_parpadeo_no_avisa_y_dos_fallas_si(entorno):
    _, enviados, estado, reloj = entorno
    estado[_url("Dashboard (dev)")] = "HTTP 502"
    pf.handler({}, None)
    assert enviados == []
    reloj["ms"] += 300_000
    r = pf.handler({}, None)
    assert r["avisos"] == 1
    plantilla, idioma, vars_ = enviados[0]
    assert plantilla == "platform_incident" and idioma == "en"
    assert len(vars_) == 8 and all(vars_)
    assert vars_[0] == "Dashboard (dev)" and vars_[1] == "HTTP 502"
    assert "dev.quinta.tech" in vars_[2] and "2 revisiones" in vars_[2]
    assert "Admin: OK" in vars_[3:]


def test_no_repite_el_aviso_hasta_6_horas(entorno):
    _, enviados, estado, reloj = entorno
    estado[_url("Admin")] = "Sin respuesta en 15 s"
    for _ in range(5):
        pf.handler({}, None)
        reloj["ms"] += 300_000
    assert len(enviados) == 1
    reloj["ms"] += pf.REPETIR_CADA_MS
    pf.handler({}, None)
    assert len(enviados) == 2


def test_al_volver_se_reinicia(entorno):
    db, enviados, estado, reloj = entorno
    url = _url("Admin")
    estado[url] = "HTTP 500"
    pf.handler({}, None); pf.handler({}, None)
    estado.pop(url)
    pf.handler({}, None)
    fila = db.conn.execute("SELECT fallas, caido_desde, ultimo_aviso FROM platform_health "
                           "WHERE service = 'Admin'").fetchone()
    assert fila == (0, None, None)
    estado[url] = "HTTP 500"
    pf.handler({}, None); pf.handler({}, None)
    assert len(enviados) == 2


def test_otro_caido_aparece_en_el_estado(entorno):
    _, enviados, estado, _ = entorno
    estado[_url("Admin")] = "HTTP 500"
    estado[_url("Dashboard (dev)")] = "HTTP 500"
    pf.handler({}, None); pf.handler({}, None)
    assert len(enviados) == 2
    vars_admin = [v for _, _, v in enviados if v[0] == "Admin"][0]
    assert "Dashboard (dev): caído" in vars_admin


def test_apagado_no_manda(entorno, monkeypatch):
    _, enviados, estado, _ = entorno
    monkeypatch.setitem(pf.CONFIG, "enabled", False)
    estado[_url("Admin")] = "HTTP 500"
    pf.handler({}, None); pf.handler({}, None)
    assert enviados == []


def test_prueba_manda_una_vez_sin_tocar_la_tabla(entorno):
    db, enviados, _, _ = entorno
    r = pf.handler({"prueba": True}, None)
    assert r["enviados"] == 1 and enviados[0][2][0] == "Prueba de alerta"
    assert db.conn.execute("SELECT COUNT(*) FROM platform_health").fetchone() == (0,)
