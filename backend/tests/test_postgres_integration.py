import os

import pytest

from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database


@pytest.mark.postgres
def test_configured_postgres_accepts_a_real_query() -> None:
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise the configured PostgreSQL service")

    settings = Settings()
    database_url = settings.resolved_test_database_url()
    ensure_test_database(database_url)
    probe = DatabaseProbe(
        database_url,
        settings.database_connect_timeout_seconds,
    )
    try:
        result = probe.check()
    finally:
        probe.close()

    assert result.status == "connected", result.detail
