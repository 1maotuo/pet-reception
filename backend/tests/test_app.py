from fastapi.testclient import TestClient

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.schemas import DatabaseStatus


class StubProbe:
    def __init__(self, result: DatabaseStatus) -> None:
        self.result = result
        self.closed = False

    def check(self) -> DatabaseStatus:
        return self.result

    def close(self) -> None:
        self.closed = True


def settings() -> Settings:
    return Settings(
        database_url="",
        voice_gateway_service_token="test-service-token",
        demo_merchant_id="demo-grooming-001",
    )


def test_health_reports_real_database_state() -> None:
    probe = StubProbe(DatabaseStatus(status="unavailable", detail="test database offline"))
    with TestClient(create_app(settings(), probe)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "pet-reception-backend",
        "status": "degraded",
        "database": {
            "status": "unavailable",
            "detail": "test database offline",
        },
    }
    assert probe.closed is True


def test_readiness_is_unavailable_without_database() -> None:
    probe = StubProbe(DatabaseStatus(status="not_configured", detail="DATABASE_URL 未配置"))
    with TestClient(create_app(settings(), probe)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["database"]["status"] == "not_configured"


def test_voice_tool_rejects_untrusted_callers() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe)) as client:
        response = client.get(
            "/api/internal/voice/merchant-info",
            headers={
                "X-Voice-Owner-ID": "user_test",
                "X-Voice-Session-ID": "session-a",
                "X-Voice-Turn-ID": "turn-a",
                "X-Merchant-ID": "demo-grooming-001",
            },
        )

    assert response.status_code == 401


def test_voice_tool_uses_gateway_bound_context() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe)) as client:
        response = client.get(
            "/api/internal/voice/merchant-info",
            headers={
                "Authorization": "Bearer test-service-token",
                "X-Voice-Owner-ID": "user_signed-owner",
                "X-Voice-Session-ID": "session-a",
                "X-Voice-Turn-ID": "turn-a",
                "X-Merchant-ID": "demo-grooming-001",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["merchant"]["id"] == "demo-grooming-001"
    assert body["merchant"]["is_demo"] is True
    assert body["merchant"]["source"] == "m1-static-demo-catalog:demo-grooming-001"
    assert body["context"] == {
        "merchant_id": "demo-grooming-001",
        "owner_id": "user_signed-owner",
        "voice_session_id": "session-a",
        "turn_id": "turn-a",
        "source": "trusted_gateway_headers",
    }


def test_voice_tool_rejects_a_different_merchant_binding() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe)) as client:
        response = client.get(
            "/api/internal/voice/merchant-info",
            headers={
                "Authorization": "Bearer test-service-token",
                "X-Voice-Owner-ID": "user_signed-owner",
                "X-Voice-Session-ID": "session-a",
                "X-Voice-Turn-ID": "turn-a",
                "X-Merchant-ID": "model-invented-merchant",
            },
        )

    assert response.status_code == 403
