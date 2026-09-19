import os

import pytest

from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe


@pytest.mark.postgres
def test_configured_postgres_accepts_a_real_query() -> None:
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise the configured PostgreSQL service")

    settings = Settings()
    probe = DatabaseProbe(
        settings.database_url,
        settings.database_connect_timeout_seconds,
    )
    try:
        result = probe.check()
    finally:
        probe.close()

    assert result.status == "connected", result.detail
