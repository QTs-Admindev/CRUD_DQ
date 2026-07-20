from urllib.parse import urlparse

import pymysql

from shared.secrets.manager import get_secret_json

_conn = None


def get_db():
    global _conn
    if _conn is None or not _conn.open:
        # El secreto MYSQL_URI guarda una URI estilo SQLAlchemy
        # (mysql+mysqlconnector://...); PyMySQL solo necesita los componentes,
        # así que el driver del esquema se ignora.
        url = urlparse(get_secret_json("MYSQL_URI")["MYSQL_URI"])
        _conn = pymysql.connect(
            host=url.hostname,
            port=url.port or 3306,
            user=url.username,
            password=url.password,
            db=url.path.lstrip("/"),
            charset="utf8mb4",
            autocommit=False,
            # READ COMMITTED: cada SELECT ve el último commit. Sin esto, la conexión
            # warm del Lambda mantiene un snapshot REPEATABLE READ congelado y /list
            # devuelve datos viejos (no ve filas creadas por otras invocaciones
            # después de su primer read).
            init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        )
    return _conn
