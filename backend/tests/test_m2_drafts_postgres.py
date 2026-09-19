import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.schemas import DemoMerchant, MerchantService
from pet_reception_backend.store import PostgresStore


pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def client() -> TestClient:
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise the M2 PostgreSQL store")

    base_settings = Settings()
    database_url = base_settings.resolved_test_database_url()
    ensure_test_database(database_url)
    setup_database(database_url, base_settings.merchant_config_path)
    configured = Settings(
        database_url=database_url,
        test_database_url=database_url,
        voice_gateway_service_token="m2-test-service-token",
        demo_merchant_id="demo-grooming-001",
        merchant_config_path=base_settings.merchant_config_path,
    )
    probe = DatabaseProbe(database_url)
    store = PostgresStore(probe.get_engine())
    store.upsert_merchant(
        DemoMerchant(
            id="other-grooming-002",
            display_name="Other Test Grooming",
            merchant_type="grooming",
            time_zone="America/New_York",
            phone="+1-212-555-0100",
            address="999 Test Avenue",
            services=[
                MerchantService(
                    id="other-service",
                    name_zh="其他商家服务",
                    duration_minutes=45,
                    price_note="测试库专用",
                )
            ],
            source="m2-test-only:other-grooming-002",
        )
    )
    with TestClient(
        create_app(configured, probe, store)
    ) as test_client:
        yield test_client


def headers(owner_id: str | None = None, session_id: str | None = None) -> dict[str, str]:
    suffix = uuid4().hex
    return {
        "Authorization": "Bearer m2-test-service-token",
        "X-Merchant-ID": "demo-grooming-001",
        "X-Voice-Owner-ID": owner_id or f"owner_{suffix}",
        "X-Voice-Session-ID": session_id or f"session_{suffix}",
        "X-Voice-Turn-ID": f"turn_{suffix}",
    }


def test_seeded_merchant_is_read_from_postgres(client: TestClient) -> None:
    response = client.get("/api/internal/voice/merchant-info", headers=headers())

    assert response.status_code == 200
    merchant = response.json()["merchant"]
    assert merchant["id"] == "demo-grooming-001"
    assert [service["id"] for service in merchant["services"]] == [
        "demo-bath",
        "demo-full-groom",
    ]
    assert merchant["grooming_policy"]["supported_species"] == ["犬"]
    assert merchant["grooming_policy"]["unsupported_species"] == [
        "猫",
        "爬行动物",
        "鸟类",
        "其他非犬宠物",
    ]
    assert merchant["business_hours"]["closed_weekdays"] == [6]
    assert merchant["business_hours"]["weekly_hours"][0] == {
        "weekday": 0,
        "opens_at": "09:00:00",
        "closes_at": "17:00:00",
    }
    assert merchant["staff"][0]["public_intro"]
    assert merchant["staff"][0]["specialties"]
    bath, full_groom = merchant["services"]
    assert bath["supported_species"] == ["犬"]
    assert "洗澡" in bath["included_items"]
    assert "毛发没有明显打结" in bath["recommended_for"]
    assert "任何毛发打结" in bath["assessment_required_for"]
    assert "剪毛造型" in bath["not_included"]
    assert full_groom["source"] == "m4-demo-grooming-catalog:demo-full-groom"


def test_draft_batch_update_correction_clear_and_same_value(
    client: TestClient,
) -> None:
    trusted_headers = headers()
    initial = client.get(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
    )

    assert initial.status_code == 200
    initial_body = initial.json()
    assert initial_body["draft"]["version"] == 0
    assert initial_body["draft"]["incoming_phone"] is None
    assert initial_body["missing_for_availability"] == ["service_id", "preferred_date"]

    batch = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "customer_name": "姚女士",
            "contact_phone": "+1-415-555-0188",
            "pet_name": "皮皮",
            "pet_species": "dog",
            "pet_breed": "金毛",
            "pet_age_years": "3.5",
            "need_description": "想做基础洗护",
            "service_id": "demo-bath",
            "preferred_date": "2026-10-02",
            "preferred_time_of_day": "afternoon",
            "staff_preference_text": "Lisa",
            "staff_id": "demo-staff-lisa",
        },
    )

    assert batch.status_code == 200
    batch_body = batch.json()
    assert batch_body["draft"]["version"] == 1
    assert batch_body["draft"]["contact_phone_source"] == "voice_user_provided"
    assert batch_body["draft"]["incoming_phone"] is None
    assert batch_body["missing_for_availability"] == []
    assert batch_body["missing_for_booking"] == ["selected_availability"]
    assert set(batch_body["changed_fields"]) == {
        "customer_name",
        "contact_phone",
        "pet_name",
        "pet_species",
        "pet_breed",
        "pet_age_years",
        "need_description",
        "service_id",
        "preferred_date",
        "preferred_time_of_day",
        "staff_preference_text",
        "staff_id",
    }
    assert batch_body["draft"]["availability_revision"] == 1

    correction = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 1, "pet_name": "比比"},
    )
    assert correction.status_code == 200
    corrected = correction.json()
    assert corrected["draft"]["version"] == 2
    assert corrected["draft"]["pet_name"] == "比比"
    assert corrected["draft"]["customer_name"] == "姚女士"
    assert corrected["draft"]["service_id"] == "demo-bath"
    assert corrected["draft"]["availability_revision"] == 1
    assert corrected["changed_fields"] == ["pet_name"]

    same_value = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 2, "pet_name": "比比"},
    )
    assert same_value.status_code == 200
    assert same_value.json()["draft"]["version"] == 2
    assert same_value.json()["changed_fields"] == []

    cleared = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 2,
            "clear_fields": ["pet_breed", "staff_preference_text"],
        },
    )
    assert cleared.status_code == 200
    cleared_body = cleared.json()
    assert cleared_body["draft"]["version"] == 3
    assert cleared_body["draft"]["pet_breed"] is None
    assert cleared_body["draft"]["staff_preference_text"] is None

    stale = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 2, "pet_name": "旧请求"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "draft_version_conflict",
        "current_version": 3,
    }


def test_age_set_with_old_representation_clear_keeps_the_new_age(
    client: TestClient,
) -> None:
    trusted_headers = headers()
    initial = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 0, "pet_age_years": 6},
    )
    assert initial.status_code == 200, initial.text

    updated = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 1,
            "pet_age_text": "四个月",
            "clear_fields": ["pet_age_years"],
        },
    )
    assert updated.status_code == 200, updated.text
    draft = updated.json()["draft"]
    assert draft["pet_age_text"] == "四个月"
    assert draft["pet_age_months"] == 4
    assert draft["pet_age_years"] is None

    cleared = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": draft["version"],
            "clear_fields": ["pet_age_months"],
        },
    )
    assert cleared.status_code == 200, cleared.text
    cleared_draft = cleared.json()["draft"]
    assert cleared_draft["pet_age_text"] is None
    assert cleared_draft["pet_age_months"] is None
    assert cleared_draft["pet_age_years"] is None


def test_draft_rejects_implicit_null_and_unknown_service(client: TestClient) -> None:
    trusted_headers = headers()

    implicit_null = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 0, "pet_name": None},
    )
    assert implicit_null.status_code == 422

    unknown_service = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 0, "service_id": "model-invented-service"},
    )
    assert unknown_service.status_code == 422
    assert unknown_service.json()["detail"]["code"] == "unknown_service"

    other_merchant_service = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 0, "service_id": "other-service"},
    )
    assert other_merchant_service.status_code == 422
    assert other_merchant_service.json()["detail"] == {
        "code": "unknown_service",
        "service_id": "other-service",
    }

    current = client.get(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
    )
    assert current.json()["draft"]["version"] == 0
    assert current.json()["draft"]["service_id"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"contact_phone": "incoming_phone"},
        {"contact_phone": "当前电话"},
        {"contact_phone": "123"},
        {"pet_name": "未知"},
        {"pet_name": "unknown"},
    ],
)
def test_draft_rejects_placeholder_contact_and_pet_values(
    client: TestClient,
    payload: dict,
) -> None:
    response = client.patch(
        "/api/internal/voice/booking-draft",
        headers=headers(),
        json={"expected_version": 0, **payload},
    )

    assert response.status_code == 422


def test_draft_rejects_identity_and_provenance_in_request_body(
    client: TestClient,
) -> None:
    trusted_headers = headers()

    for forbidden_field, value in (
        ("merchant_id", "model-invented-merchant"),
        ("owner_id", "model-invented-owner"),
        ("voice_session_id", "model-invented-session"),
        ("contact_phone_source", "web_user_provided"),
        ("incoming_phone", "+1-415-555-0100"),
    ):
        response = client.patch(
            "/api/internal/voice/booking-draft",
            headers=trusted_headers,
            json={
                "expected_version": 0,
                "pet_name": "不会写入",
                forbidden_field: value,
            },
        )
        assert response.status_code == 422

    current = client.get(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
    )
    assert current.json()["draft"]["version"] == 0
    assert current.json()["draft"]["pet_name"] is None


def test_drafts_are_isolated_by_trusted_owner_and_session(client: TestClient) -> None:
    owner_a = f"owner_{uuid4().hex}"
    session_a = f"session_{uuid4().hex}"
    first_headers = headers(owner_a, session_a)
    first = client.patch(
        "/api/internal/voice/booking-draft",
        headers=first_headers,
        json={"expected_version": 0, "pet_name": "只属于会话 A"},
    )
    assert first.status_code == 200

    other_owner = client.get(
        "/api/internal/voice/booking-draft",
        headers=headers(f"owner_{uuid4().hex}", session_a),
    ).json()["draft"]
    other_session = client.get(
        "/api/internal/voice/booking-draft",
        headers=headers(owner_a, f"session_{uuid4().hex}"),
    ).json()["draft"]

    assert other_owner["id"] != first.json()["draft"]["id"]
    assert other_owner["pet_name"] is None
    assert other_session["id"] != first.json()["draft"]["id"]
    assert other_session["pet_name"] is None


def test_draft_endpoint_rejects_model_supplied_merchant_binding(
    client: TestClient,
) -> None:
    forged = headers()
    forged["X-Merchant-ID"] = "model-invented-merchant"

    response = client.get(
        "/api/internal/voice/booking-draft",
        headers=forged,
    )

    assert response.status_code == 403
