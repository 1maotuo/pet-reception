import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.merchant_catalog import load_demo_merchant
from pet_reception_backend.schemas import DatabaseStatus, DemoMerchant
from pet_reception_backend.service_configuration import calculate_service_quote


class StubProbe:
    def __init__(self, result: DatabaseStatus) -> None:
        self.result = result
        self.closed = False

    def check(self) -> DatabaseStatus:
        return self.result

    def close(self) -> None:
        self.closed = True


class StubStore:
    def __init__(self, merchant: DemoMerchant) -> None:
        self.merchant = merchant

    def get_merchant(self, merchant_id: str) -> DemoMerchant | None:
        return self.merchant if merchant_id == self.merchant.id else None

    def quote_service(self, context, request):
        merchant = self.get_merchant(context.merchant_id)
        if merchant is None:
            raise LookupError(context.merchant_id)
        return calculate_service_quote(merchant, request)

    def get_or_create_draft(self, _context):
        raise NotImplementedError

    def update_draft(self, _context, _expected_version, _changes):
        raise NotImplementedError


def settings() -> Settings:
    return Settings(
        database_url="",
        voice_gateway_service_token="test-service-token",
        demo_merchant_id="demo-grooming-001",
        merchant_config_path="config/demo-merchant.json",
    )


def default_store() -> StubStore:
    return StubStore(load_demo_merchant())


def write_merchant(path: Path, merchant_id: str, display_name: str) -> None:
    path.write_text(
        json.dumps(
            {
                "id": merchant_id,
                "display_name": display_name,
                "merchant_type": "grooming",
                "time_zone": "America/Los_Angeles",
                "phone": "+1-415-555-0199",
                "address": "456 Review Street",
                "services": [
                    {
                        "id": "review-bath",
                        "name_zh": "复核洗护",
                        "duration_minutes": 75,
                        "price_note": "演示价格：$65 起",
                    }
                ],
                "source": f"test-catalog:{merchant_id}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_health_reports_real_database_state() -> None:
    probe = StubProbe(DatabaseStatus(status="unavailable", detail="test database offline"))
    with TestClient(create_app(settings(), probe, default_store())) as client:
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
    with TestClient(create_app(settings(), probe, default_store())) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["database"]["status"] == "not_configured"


def test_voice_tool_rejects_untrusted_callers() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe, default_store())) as client:
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


def test_booking_draft_rejects_untrusted_callers_before_store_access() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe, default_store())) as client:
        response = client.get(
            "/api/internal/voice/booking-draft",
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
    with TestClient(create_app(settings(), probe, default_store())) as client:
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
    assert body["merchant_local_date"]
    assert body["merchant_local_datetime"]
    assert body["context"] == {
        "merchant_id": "demo-grooming-001",
        "owner_id": "user_signed-owner",
        "voice_session_id": "session-a",
        "turn_id": "turn-a",
        "source": "trusted_gateway_headers",
    }


def test_voice_tool_rejects_a_different_merchant_binding() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    with TestClient(create_app(settings(), probe, default_store())) as client:
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


def test_read_only_quote_uses_trusted_merchant_and_rejects_derived_facts() -> None:
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))
    trusted_headers = {
        "Authorization": "Bearer test-service-token",
        "X-Voice-Owner-ID": "user_signed-owner",
        "X-Voice-Session-ID": "session-quote",
        "X-Voice-Turn-ID": "turn-quote",
        "X-Merchant-ID": "demo-grooming-001",
    }
    with TestClient(create_app(settings(), probe, default_store())) as client:
        response = client.post(
            "/api/internal/voice/service-quotes",
            headers=trusted_headers,
            json={"service_id": "demo-bath"},
        )
        forged = client.post(
            "/api/internal/voice/service-quotes",
            headers=trusted_headers,
            json={
                "service_id": "demo-bath",
                "merchant_id": "other-merchant",
                "total_min_cents": 1,
                "scheduling_duration_minutes": 1,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["quote"]["merchant_id"] == "demo-grooming-001"
    assert body["quote"]["total_semantics"] == "unconfigured"
    assert body["quote"]["total_min_cents"] is None
    assert body["quote"]["legacy_price_note"].startswith("演示价格")
    assert body["context"]["voice_session_id"] == "session-quote"
    assert forged.status_code == 422


def test_voice_tool_uses_a_store_matching_the_configured_merchant(tmp_path: Path) -> None:
    merchant_path = tmp_path / "merchant-b.json"
    write_merchant(merchant_path, "review-grooming-002", "Review Grooming B")
    configured = Settings(
        database_url="",
        voice_gateway_service_token="test-service-token",
        demo_merchant_id="review-grooming-002",
        merchant_config_path=str(merchant_path),
    )
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))

    with TestClient(
        create_app(configured, probe, StubStore(load_demo_merchant(merchant_path)))
    ) as client:
        response = client.get(
            "/api/internal/voice/merchant-info",
            headers={
                "Authorization": "Bearer test-service-token",
                "X-Voice-Owner-ID": "user_signed-owner",
                "X-Voice-Session-ID": "session-b",
                "X-Voice-Turn-ID": "turn-b",
                "X-Merchant-ID": "review-grooming-002",
            },
        )

    assert response.status_code == 200
    assert response.json()["merchant"]["display_name"] == "Review Grooming B"


def test_voice_tool_returns_current_store_facts_for_the_bound_id(tmp_path: Path) -> None:
    merchant_path = tmp_path / "merchant-updated.json"
    write_merchant(merchant_path, "demo-grooming-001", "Updated Demo Grooming")
    configured = Settings(
        database_url="",
        voice_gateway_service_token="test-service-token",
        demo_merchant_id="demo-grooming-001",
        merchant_config_path=str(merchant_path),
    )
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))

    with TestClient(
        create_app(configured, probe, StubStore(load_demo_merchant(merchant_path)))
    ) as client:
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
    assert response.json()["merchant"]["display_name"] == "Updated Demo Grooming"


def test_backend_rejects_a_merchant_id_that_does_not_match_the_file(
    tmp_path: Path,
) -> None:
    merchant_path = tmp_path / "merchant-mismatch.json"
    write_merchant(merchant_path, "review-grooming-002", "Review Grooming B")
    configured = Settings(
        database_url="",
        voice_gateway_service_token="test-service-token",
        demo_merchant_id="demo-grooming-001",
        merchant_config_path=str(merchant_path),
    )
    probe = StubProbe(DatabaseStatus(status="connected", detail="ok"))

    with pytest.raises(
        ValueError,
        match="DEMO_MERCHANT_ID must match the trusted merchant configuration",
    ):
        create_app(configured, probe)
