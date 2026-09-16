from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Base


# Kolom yang ditambahkan setelah versi pertama. `create_all` hanya membuat tabel yang belum ada dan
# tidak pernah menyentuh tabel lama, jadi database yang sudah dipakai butuh penambahan eksplisit.
# Sengaja terbatas pada kolom nullable: itu satu-satunya perubahan yang aman tanpa menulis ulang data.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "screened_events": {
        "dedupe_key": "VARCHAR",
        "updated_at": "DATETIME",
    },
}

ADDED_INDEXES: dict[str, str] = {
    "ix_screened_events_dedupe_key":
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_screened_events_dedupe_key "
        "ON screened_events (dedupe_key)",
}


def ensure_schema(engine) -> list[str]:
    """Tambahkan kolom dan indeks yang belum ada pada database lama. Mengembalikan yang diterapkan."""
    applied: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, kind in columns.items():
                if name not in present:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                    applied.append(f"{table}.{name}")
        for name, statement in ADDED_INDEXES.items():
            connection.exec_driver_sql(statement)
            applied.append(name)
    return applied


def build_engine(settings: Settings):
    engine = create_engine(f"sqlite:///{settings.signalgate_db_path}")
    Base.metadata.create_all(engine)
    ensure_schema(engine)
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
