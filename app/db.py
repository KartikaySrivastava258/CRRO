from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

class Base(DeclarativeBase): pass

settings=get_settings()
engine=create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal=sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

def init_db():
    from . import models
    Base.metadata.create_all(bind=engine)

def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()
