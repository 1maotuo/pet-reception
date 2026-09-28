import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.merchant_catalog import load_demo_merchant
from pet_reception_backend.store import PostgresStore


pytestmark = pytest.mark.postgres
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-grooming-merchant.json"
MERCHANT_ID = "synthetic-grooming-test-001"


@pytest.fixture(scope="module")
def client() -> TestClient:
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise combination bookings")
    base = Settings()
    database_url = base.resolved_test_database_url()
    ensure_test_database(database_url)
    setup_database(database_url, base.merchant_config_path)
    settings = Settings(
        database_url=database_url,
        test_database_url=database_url,
        voice_gateway_service_token="combination-test-token",
        booking_credential_secret="combination-test-secret-at-least-32-characters",
        demo_merchant_id=MERCHANT_ID,
        merchant_config_path=str(FIXTURE),
    )
    probe = DatabaseProbe(database_url)
    store = PostgresStore(
        probe.get_engine(),
        booking_credential_secret=settings.booking_credential_secret,
    )
    store.upsert_merchant(load_demo_merchant(FIXTURE))
    with TestClient(create_app(settings, probe, store)) as test_client:
        yield test_client


def headers(owner: str, session: str, turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer combination-test-token",
        "X-Merchant-ID": MERCHANT_ID,
        "X-Voice-Owner-ID": owner,
        "X-Voice-Session-ID": session,
        "X-Voice-Turn-ID": turn,
    }


def future_date() -> str:
    return (datetime.now(ZoneInfo("America/Los_Angeles")).date() + timedelta(days=14)).isoformat()


def test_price_only_weight_does_not_block_real_availability(client: TestClient) -> None:
    suffix = uuid4().hex
    request_headers = headers(f"owner-{suffix}", f"session-{suffix}", "turn-1")
    draft = client.get("/api/internal/voice/booking-draft", headers=request_headers).json()
    updated = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": draft["draft"]["version"],
            "service_id": "synthetic-dog-bath",
            "pet_species": "犬",
            "preferred_date": future_date(),
        },
    ).json()
    assert "weight" in updated["service_quote"]["missing_for_quote"]
    assert "weight" not in updated["missing_for_availability"]
    searched = client.post(
        "/api/internal/voice/availability-searches",
        headers=request_headers,
        json={
            "expected_availability_revision": updated["draft"]["availability_revision"]
        },
    )
    assert searched.status_code == 200
    assert searched.json()["search"]["scheduling_duration_minutes"] == 60


def test_combination_duration_summary_and_fixed_deposit_are_frozen(client: TestClient) -> None:
    suffix = uuid4().hex
    owner = f"owner-{suffix}"
    session = f"session-{suffix}"
    request_headers = headers(owner, session, "turn-1")
    draft = client.get("/api/internal/voice/booking-draft", headers=request_headers).json()
    initial = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": draft["draft"]["version"],
            "service_id": "synthetic-dog-haircut",
            "pet_species": "犬",
            "pet_weight": {"value": "35", "unit": "lb", "estimated": False},
            "preferred_date": future_date(),
            "preferred_time_of_day": "morning",
        },
    ).json()
    first_search = client.post(
        "/api/internal/voice/availability-searches",
        headers=request_headers,
        json={"expected_availability_revision": initial["draft"]["availability_revision"]},
    ).json()["search"]
    assert first_search["scheduling_duration_minutes"] == 120

    combined = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": initial["draft"]["version"],
            "selected_addon_ids": ["nail-trim", "teeth-brushing", "teeth-brushing"],
        },
    ).json()
    assert combined["draft"]["selected_addon_ids"] == ["nail-trim", "teeth-brushing"]
    assert combined["service_quote"]["scheduling_duration_minutes"] == 130
    assert combined["service_quote"]["total_min_cents"] == 11700
    assert combined["service_quote"]["deposit_amount_cents"] == 3000
    stale = client.get(
        f"/api/internal/voice/availability-searches/{first_search['id']}",
        headers=request_headers,
    ).json()["search"]
    assert stale["status"] == "stale"

    completed = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": combined["draft"]["version"],
            "customer_name": "测试客户",
            "contact_phone": "18100000000",
            "pet_name": "元宝",
        },
    ).json()
    second_search = client.post(
        "/api/internal/voice/availability-searches",
        headers=request_headers,
        json={"expected_availability_revision": completed["draft"]["availability_revision"]},
    ).json()["search"]
    assert second_search["scheduling_duration_minutes"] == 130
    candidate = second_search["candidates"][0]
    assert (
        datetime.fromisoformat(candidate["ends_at"])
        - datetime.fromisoformat(candidate["starts_at"])
    ) == timedelta(minutes=130)

    prepared = client.post(
        "/api/internal/voice/booking-preparations",
        headers=request_headers,
        json={
            "expected_version": completed["draft"]["version"],
            "candidate_id": candidate["id"],
        },
    ).json()
    summary = prepared["preparation"]["summary"]
    assert summary["service_quote"]["total_min_cents"] == 11700
    assert summary["service_quote"]["deposit_amount_cents"] == 3000
    assert summary["selected_addon_ids"] == ["nail-trim", "teeth-brushing"]

    version = prepared["preparation"]["draft_version"]
    preparation_id = prepared["preparation"]["id"]
    presented = client.post(
        f"/api/internal/voice/booking-preparations/{preparation_id}/presented",
        headers={**headers(owner, session, "turn-1"), "X-Presentation-Event-ID": f"present-{suffix}"},
        json={"expected_draft_version": version},
    )
    assert presented.status_code == 200
    confirmed = client.post(
        f"/api/internal/voice/booking-preparations/{preparation_id}/confirm",
        headers={**headers(owner, session, "turn-2"), "X-Confirmation-Event-ID": f"confirm-{suffix}"},
        json={"expected_draft_version": version},
    ).json()
    assert confirmed["status"] == "awaiting_payment"
    assert confirmed["booking"]["deposit_amount_cents"] == 3000
    assert confirmed["booking"]["payment_amount_cents"] is None

    credential = confirmed["customer_credential"]
    paid = client.post(
        f"/api/customer/bookings/{credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {credential['access_token']}",
            "X-Payment-Event-ID": f"payment-{suffix}",
        },
    ).json()["booking"]
    assert paid["status"] == "confirmed"
    assert paid["payment_amount_cents"] == 3000
    assert paid["payment_currency"] == "USD"


def test_incompatible_addon_is_rejected_and_empty_list_clears(client: TestClient) -> None:
    suffix = uuid4().hex
    request_headers = headers(f"owner-{suffix}", f"session-{suffix}", "turn-1")
    draft = client.get("/api/internal/voice/booking-draft", headers=request_headers).json()
    bad = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": draft["draft"]["version"],
            "service_id": "synthetic-cat-groom",
            "pet_species": "猫",
            "selected_addon_ids": ["teeth-brushing"],
        },
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "addon_not_applicable"

    good = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": draft["draft"]["version"],
            "service_id": "synthetic-cat-groom",
            "pet_species": "猫",
            "selected_addon_ids": ["cat-deshedding"],
        },
    ).json()
    cleared = client.patch(
        "/api/internal/voice/booking-draft",
        headers=request_headers,
        json={
            "expected_version": good["draft"]["version"],
            "selected_addon_ids": [],
        },
    ).json()
    assert cleared["draft"]["selected_addon_ids"] == []
