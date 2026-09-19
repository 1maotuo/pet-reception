import argparse
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .config import Settings
from .database import DatabaseProbe
from .migrations import run_migrations
from .seed import seed_demo_merchant
from .store import PostgresStore


TEST_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+_test$")


def require_database_url(database_url: str, variable_name: str) -> str:
    if not database_url:
        raise RuntimeError(f"{variable_name} is required")
    return database_url


def ensure_test_database(database_url: str) -> bool:
    url = make_url(require_database_url(database_url, "TEST_DATABASE_URL"))
    database = url.database or ""
    if not TEST_DATABASE_NAME.fullmatch(database):
        raise RuntimeError("test database name must end with _test")
    admin_url = url.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database"),
                {"database": database},
            ).scalar_one_or_none()
            if exists:
                return False
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
            return True
    finally:
        engine.dispose()


def setup_database(database_url: str, merchant_config_path: str) -> tuple[list[str], str]:
    probe = DatabaseProbe(database_url)
    try:
        engine = probe.get_engine()
        migrations = run_migrations(engine)
        merchant_id = seed_demo_merchant(
            PostgresStore(engine),
            merchant_config_path,
        )
        return migrations, merchant_id
    finally:
        probe.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pet Reception database management")
    parser.add_argument("command", choices=["migrate", "seed", "setup", "init-test-db"])
    args = parser.parse_args()
    settings = Settings()

    if args.command == "init-test-db":
        database_url = settings.resolved_test_database_url()
        created = ensure_test_database(database_url)
        migrations, merchant_id = setup_database(
            database_url,
            settings.merchant_config_path,
        )
        print(
            f"test database {'created' if created else 'already existed'}; "
            f"migrations={migrations or ['none']}; merchant={merchant_id}"
        )
        return

    database_url = require_database_url(settings.database_url, "DATABASE_URL")
    probe = DatabaseProbe(database_url)
    try:
        engine = probe.get_engine()
        migrations: list[str] = []
        merchant_id = ""
        if args.command in {"migrate", "setup"}:
            migrations = run_migrations(engine)
        if args.command in {"seed", "setup"}:
            merchant_id = seed_demo_merchant(
                PostgresStore(engine),
                settings.merchant_config_path,
            )
        print(
            f"migrations={migrations or ['none']}; "
            f"merchant={merchant_id or 'not-seeded'}"
        )
    finally:
        probe.close()


if __name__ == "__main__":
    main()
