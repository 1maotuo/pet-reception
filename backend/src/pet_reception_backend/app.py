from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .database import DatabaseProbe
from .merchant_catalog import get_demo_merchant
from .schemas import (
    DatabaseStatus,
    MerchantToolResult,
    SystemStatus,
    VoiceRequestContext,
)


class Probe(Protocol):
    def check(self) -> DatabaseStatus: ...

    def close(self) -> None: ...


bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: Settings | None = None,
    database_probe: Probe | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    probe = database_probe or DatabaseProbe(
        resolved_settings.database_url,
        resolved_settings.database_connect_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        probe.close()

    application = FastAPI(
        title="Pet Reception Backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved_settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def current_status() -> SystemStatus:
        database = probe.check()
        return SystemStatus(
            status="ok" if database.status == "connected" else "degraded",
            database=database,
        )

    @application.get("/health", response_model=SystemStatus)
    def health() -> SystemStatus:
        return current_status()

    @application.get("/readyz", response_model=SystemStatus)
    def readiness(response: Response) -> SystemStatus:
        result = current_status()
        if result.status != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @application.get("/api/system/status", response_model=SystemStatus)
    def system_status() -> SystemStatus:
        return current_status()

    @application.get(
        "/api/internal/voice/merchant-info",
        response_model=MerchantToolResult,
    )
    def merchant_info(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_voice_owner_id: str = Header(min_length=6, max_length=128),
        x_voice_session_id: str = Header(min_length=1, max_length=128),
        x_voice_turn_id: str = Header(min_length=1, max_length=128),
        x_merchant_id: str = Header(min_length=1, max_length=128),
    ) -> MerchantToolResult:
        expected_token = resolved_settings.voice_gateway_service_token
        supplied_token = credentials.credentials if credentials else ""
        if (
            not expected_token
            or not supplied_token
            or not compare_digest(supplied_token, expected_token)
        ):
            raise HTTPException(status_code=401, detail="voice gateway authentication failed")
        if x_merchant_id != resolved_settings.demo_merchant_id:
            raise HTTPException(status_code=403, detail="merchant is not bound to this gateway")
        merchant = get_demo_merchant(x_merchant_id)
        if merchant is None:
            raise HTTPException(status_code=404, detail="merchant not found")
        return MerchantToolResult(
            merchant=merchant,
            context=VoiceRequestContext(
                merchant_id=merchant.id,
                owner_id=x_voice_owner_id,
                voice_session_id=x_voice_session_id,
                turn_id=x_voice_turn_id,
            ),
        )

    return application


app = create_app()
