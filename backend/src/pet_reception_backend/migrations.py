from collections.abc import Callable

from sqlalchemy import Column, DateTime, MetaData, String, Table, func, select
from sqlalchemy.engine import Connection, Engine

from .models import metadata


migration_metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    migration_metadata,
    Column("version", String(100), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def create_m2_schema(connection: Connection) -> None:
    metadata.create_all(connection)


MIGRATIONS: tuple[tuple[str, Callable[[Connection], None]], ...] = (
    ("0001_m2_merchant_and_booking_drafts", create_m2_schema),
)


def run_migrations(engine: Engine) -> list[str]:
    migration_metadata.create_all(engine)
    applied_now: list[str] = []
    with engine.begin() as connection:
        applied = set(connection.execute(select(schema_migrations.c.version)).scalars())
        for version, migration in MIGRATIONS:
            if version in applied:
                continue
            migration(connection)
            connection.execute(schema_migrations.insert().values(version=version))
            applied_now.append(version)
    return applied_now
