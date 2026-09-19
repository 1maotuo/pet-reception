from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .schemas import DatabaseStatus


@dataclass(slots=True)
class DatabaseProbe:
    database_url: str
    connect_timeout_seconds: int = 2
    _engine: Engine | None = None

    def get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": self.connect_timeout_seconds},
            )
        return self._engine

    def check(self) -> DatabaseStatus:
        if not self.database_url:
            return DatabaseStatus(
                status="not_configured",
                detail="DATABASE_URL 未配置",
            )
        try:
            with self.get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            return DatabaseStatus(status="connected", detail="PostgreSQL 连接正常")
        except Exception as error:  # The public response deliberately omits DSN details.
            return DatabaseStatus(
                status="unavailable",
                detail=f"PostgreSQL 连接失败（{type(error).__name__}）",
            )

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
