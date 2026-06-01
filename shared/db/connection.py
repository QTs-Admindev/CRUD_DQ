import json

import pymysql

from shared.secrets.manager import get_secret

_conn = None


def get_db():
    global _conn
    if _conn is None or not _conn.open:
        config = json.loads(get_secret("MYSQL_DB_URI"))
        _conn = pymysql.connect(
            host=config["host"],
            user=config["user"],
            password=config["password"],
            db=config["db"],
            charset="utf8mb4",
            autocommit=False,
        )
    return _conn
