"""
Database Engine / Session
------------------------------------------------------------
Reads DATABASE_URL from the environment; defaults to the local
PostgreSQL instance set up for this demo. If the DB is unreachable,
callers (api.py) fall back to the in-memory CustomerStateStore /
AuditLog so the API still runs without Postgres.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://postgres:crro_dev_pw@localhost:5432/crro"
)

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def db_is_reachable():
    try:
        eng = get_engine()
        with eng.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False
