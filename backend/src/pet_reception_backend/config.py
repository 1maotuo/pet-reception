from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8010
    web_origin: str = "http://127.0.0.1:5173"
    database_url: str = ""
    test_database_url: str = ""
    database_connect_timeout_seconds: int = Field(default=2, ge=1, le=10)
    voice_gateway_service_token: str = ""
    merchant_admin_token: str = ""
    booking_credential_secret: str = ""
    booking_hold_minutes: int = Field(default=5, ge=1, le=30)
    demo_merchant_id: str = "demo-grooming-001"
    merchant_config_path: str = "config/demo-merchant.json"

    def resolved_test_database_url(self) -> str:
        if self.test_database_url:
            return self.test_database_url
        if not self.database_url:
            return ""
        url = make_url(self.database_url)
        database = url.database or "pet_reception"
        return url.set(database=f"{database}_test").render_as_string(
            hide_password=False
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
