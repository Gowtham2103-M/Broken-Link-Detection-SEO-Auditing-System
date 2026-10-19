"""Test support: lets the API logic run without MySQL (and without FastAPI).

* If ``mysql.connector`` is missing, a stub module is installed.
* ``database.get_connection`` is replaced by a SQLite-backed adapter that
  understands the ``%s`` placeholders and ``dictionary=True`` cursors the
  application uses. Schema creation (``ensure_schema``) is replaced by the
  equivalent SQLite DDL.
* If ``fastapi`` / ``pydantic`` are missing, minimal stand-ins are installed so
  the route *functions* can be imported and called directly.

This validates application logic. It cannot validate MySQL-specific behaviour.
"""
import os
import sqlite3
import sys
import tempfile
import types
from datetime import date, datetime


# ---------------------------------------------------------------- stubs ----
def _install_mysql_stub():
    try:
        import mysql.connector  # noqa: F401
        return
    except ImportError:
        pass
    mysql = types.ModuleType("mysql")
    connector = types.ModuleType("mysql.connector")

    class Error(Exception):
        pass

    connector.Error = Error
    connector.connect = lambda **kw: (_ for _ in ()).throw(Error("no MySQL in tests"))
    mysql.connector = connector
    sys.modules["mysql"] = mysql
    sys.modules["mysql.connector"] = connector


def _install_fastapi_stub():
    try:
        import fastapi  # noqa: F401
        import pydantic  # noqa: F401
        return
    except ImportError:
        pass

    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    class FastAPI:
        def __init__(self, **kwargs):
            self.routes = {}

        def add_middleware(self, *a, **k):
            pass

        def _route(self, method):
            def decorator_factory(path):
                def decorator(fn):
                    self.routes[(method, path)] = fn
                    return fn
                return decorator
            return decorator_factory

        def __getattr__(self, name):
            if name in ("get", "post", "put", "delete"):
                return self._route(name.upper())
            raise AttributeError(name)

    fastapi.FastAPI = FastAPI
    fastapi.HTTPException = HTTPException
    fastapi.Query = lambda default=None, **kw: default

    encoders = types.ModuleType("fastapi.encoders")

    def jsonable_encoder(obj):
        if isinstance(obj, dict):
            return {k: jsonable_encoder(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [jsonable_encoder(v) for v in obj]
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return obj

    encoders.jsonable_encoder = jsonable_encoder

    middleware = types.ModuleType("fastapi.middleware")
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = object

    responses = types.ModuleType("fastapi.responses")

    class Response:
        def __init__(self, content=b"", media_type=None, headers=None):
            self.body, self.media_type, self.headers = content, media_type, headers or {}

    class JSONResponse(Response):
        def __init__(self, content=None, headers=None):
            super().__init__(content, "application/json", headers)

    responses.Response, responses.JSONResponse = Response, JSONResponse

    pydantic = types.ModuleType("pydantic")

    def Field(default=None, **kwargs):
        return default

    class BaseModel:
        def __init__(self, **kwargs):
            for name in getattr(type(self), "__annotations__", {}):
                setattr(self, name, kwargs.get(name, getattr(type(self), name, None)))

    pydantic.Field, pydantic.BaseModel = Field, BaseModel

    for name, module in {
        "fastapi": fastapi, "fastapi.encoders": encoders,
        "fastapi.middleware": middleware, "fastapi.middleware.cors": cors,
        "fastapi.responses": responses, "pydantic": pydantic,
    }.items():
        sys.modules[name] = module


# --------------------------------------------------------------- sqlite ----
SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS scans (
  scan_id INTEGER PRIMARY KEY AUTOINCREMENT, start_url TEXT, page_limit INT,
  pages_inspected INT DEFAULT 0, issues_detected INT DEFAULT 0, scan_time TEXT,
  status TEXT DEFAULT 'completed', crawl_depth INT, links_found INT DEFAULT 0,
  links_checked INT DEFAULT 0, links_skipped INT DEFAULT 0, progress_phase TEXT,
  progress_current INT DEFAULT 0, progress_total INT DEFAULT 0, notes TEXT,
  finished_at TEXT);

CREATE TABLE IF NOT EXISTS pages (
  page_id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INT,
  url TEXT,
  http_status INT,
  final_url TEXT,
  redirect_count INT DEFAULT 0,
  title TEXT,
  error TEXT,
  depth INT,
  priority REAL,
  priority_reason TEXT,
  processed_priority REAL,
  processed_order INT
);

CREATE TABLE IF NOT EXISTS issues (
  issue_id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INT, source_url TEXT,
  target_url TEXT, issue_type TEXT, http_status INT, severity TEXT,
  status TEXT DEFAULT 'Open', category TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS rechecks (
  recheck_id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INT, http_status INT,
  final_url TEXT, redirect_count INT DEFAULT 0, issue_status TEXT, checked_at TEXT,
  error TEXT, outcome TEXT);
"""


def _adapt(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


class _Cursor:
    def __init__(self, raw, dictionary):
        self.raw, self.dictionary, self.lastrowid = raw, dictionary, None

    def execute(self, query, params=()):
        self.raw.execute(query.replace("%s", "?"), tuple(_adapt(p) for p in params))
        self.lastrowid = self.raw.lastrowid

    def executemany(self, query, rows):
        self.raw.executemany(
            query.replace("%s", "?"), [tuple(_adapt(p) for p in r) for r in rows]
        )

    def _row(self, row):
        if row is None or not self.dictionary:
            return row
        return {d[0]: row[i] for i, d in enumerate(self.raw.description)}

    def fetchone(self):
        return self._row(self.raw.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self.raw.fetchall()]

    def close(self):
        self.raw.close()


class _Connection:
    def __init__(self, path):
        self.raw = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.autocommit = False

    def cursor(self, dictionary=False):
        return _Cursor(self.raw.cursor(), dictionary)

    def start_transaction(self):
        pass

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def is_connected(self):
        return True

    def close(self):
        self.raw.close()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "autocommit" and value:
            self.raw.isolation_level = None


def install(db_path=None):
    """Install stubs, patch ``database`` and return the SQLite path."""
    _install_mysql_stub()
    _install_fastapi_stub()

    if db_path is None:
        handle, db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    boot = sqlite3.connect(db_path)
    boot.executescript(SQLITE_DDL)
    boot.commit()
    boot.close()

    import database

    database.get_connection = lambda: _Connection(db_path)
    database.ensure_schema = lambda force=False: None
    return db_path
