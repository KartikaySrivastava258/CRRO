"""
Minimal DB-API wrapper supporting SQLite (stdlib) and PostgreSQL (psycopg2).

Why not SQLAlchemy here: the ledger needs explicit transaction boundaries,
`INSERT ... ON CONFLICT DO NOTHING RETURNING` and compare-and-set UPDATEs with
row counts. Those are expressed directly in SQL that both engines accept, and
the module stays importable with the standard library alone.

SQL is written with `?` placeholders and translated to `%s` for psycopg2.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


class UniqueViolation(Exception):
    pass


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _norm_value(v):
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return v


class Database:
    """Connection factory + transaction helper.

    url examples:
      sqlite:///path/to/crro.db      (file; WAL mode, busy timeout)
      sqlite://:memory:              (single shared connection; tests only)
      postgresql://user:pw@host:5432/db  (psycopg2)
    """

    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite"):
            self.dialect = "sqlite"
            path = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else ":memory:"
            self._path = path
            self._memory_conn = None
            self._memory_lock = threading.RLock()
        elif url.startswith("postgres"):
            self.dialect = "postgresql"
            self._pg_url = url.replace("postgresql+psycopg2://", "postgresql://")
        else:
            raise ValueError(f"unsupported DATABASE_URL scheme: {url.split(':', 1)[0]}")

    # ---------------------------------------------------------------- connect
    def _connect(self):
        if self.dialect == "sqlite":
            if self._path == ":memory:":
                if self._memory_conn is None:
                    self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False,
                                                        isolation_level=None)
                    self._memory_conn.execute("PRAGMA foreign_keys=ON")
                return self._memory_conn
            conn = sqlite3.connect(self._path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        import psycopg2  # imported lazily: optional dependency
        conn = psycopg2.connect(self._pg_url, connect_timeout=5)
        conn.autocommit = False
        return conn

    @contextmanager
    def transaction(self, *, write: bool = True):
        """One unit of work. SQLite: BEGIN IMMEDIATE takes the write lock up
        front so concurrent writers serialise instead of deadlocking.
        PostgreSQL: READ COMMITTED + row-level CAS updates / unique constraints."""
        conn = self._connect()
        shared = self.dialect == "sqlite" and self._path == ":memory:"
        lock = self._memory_lock if shared else None
        if lock:
            lock.acquire()
        tx = Tx(conn, self.dialect)
        try:
            if self.dialect == "sqlite":
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield tx
            conn.commit() if self.dialect == "postgresql" else conn.execute("COMMIT")
        except BaseException:
            try:
                conn.rollback() if self.dialect == "postgresql" else conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            if not shared:
                conn.close()
            if lock:
                lock.release()

    def ping(self) -> bool:
        try:
            with self.transaction(write=False) as tx:
                tx.one("SELECT 1 AS ok")
            return True
        except Exception:
            return False


class Tx:
    def __init__(self, conn, dialect):
        self.conn = conn
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgresql" else sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        params = [json.dumps(p, sort_keys=True, default=str) if isinstance(p, (dict, list)) else p
                  for p in params]
        cur = self.conn.cursor()
        try:
            cur.execute(self._sql(sql), params)
        except Exception as e:  # normalise unique violations across drivers
            if _is_unique_violation(e):
                raise UniqueViolation(str(e)) from e
            raise
        self._last_cursor = cur
        return cur.rowcount

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        self.execute(sql, params)
        cur = self._last_cursor
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [{c: _norm_value(v) for c, v in zip(cols, row)} for row in cur.fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable[Any] = ()):
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))


def _is_unique_violation(e: Exception) -> bool:
    if isinstance(e, sqlite3.IntegrityError) and "UNIQUE" in str(e).upper():
        return True
    pgcode = getattr(e, "pgcode", None)
    return pgcode == "23505"


def loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def database_from_env() -> Database:
    return Database(os.environ.get("CRRO_DATABASE_URL")
                    or os.environ.get("DATABASE_URL", "sqlite:///./crro_demo.db"))
