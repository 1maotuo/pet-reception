"""Run the current backend against the independent PostgreSQL test database."""

import uvicorn

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings


def main() -> None:
    base = Settings()
    settings = base.model_copy(
        update={
            "app_env": "test",
            "backend_port": 8021,
            "database_url": base.resolved_test_database_url(),
            "test_database_url": base.resolved_test_database_url(),
        }
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8021)


if __name__ == "__main__":
    main()
