from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Base


def build_engine(settings: Settings):
    engine = create_engine(f"sqlite:///{settings.signalgate_db_path}")
    Base.metadata.create_all(engine)
    return engine


def build_session_factory(settings: Settings) -> sessionmaker:
    engine = build_engine(settings)
    return sessionmaker(bind=engine)


def get_session(session_factory: sessionmaker) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
