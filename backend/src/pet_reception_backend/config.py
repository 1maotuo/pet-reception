from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_connect_timeout_seconds: int = Field(default=2, ge=1, le=10)
    voice_gateway_service_token: str = ""
    demo_merchant_id: str = "demo-grooming-001"
    merchant_config_path: str = "config/demo-merchant.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
