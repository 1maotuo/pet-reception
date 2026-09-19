import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.models import booking_preparations, bookings
from pet_reception_backend.schemas import VoiceRequestContext
from pet_reception_backend.store import (
    BookingAlreadySubmittedError,
    BookingConfirmationConflictError,
    CandidateUnavailableError,
    PostgresStore,
)


pytestmark = pytest.mark.postgres

VOICE_TOKEN = "m3-test-voice-token"
ADMIN_TOKEN = "m3-test-admin-token"
CREDENTIAL_SECRET = "m3-test-credential-secret-with-at-least-32-characters"
MERCHANT_ID = "demo-grooming-001"
MERCHANT_ZONE = ZoneInfo("America/Los_Angeles")


def next_tuesday(days_ahead: int) -> date:
    start = datetime.now(MERCHANT_ZONE).date() + timedelta(days=days_ahead)
    return start + timedelta(days=(1 - start.weekday()) % 7)


@pytest.fixture(scope="module")
def environment():
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise M3 booking")

    base_settings = Settings()
    database_url = base_settings.resolved_test_database_url()
    ensure_test_database(database_url)
    setup_database(database_url, base_settings.merchant_config_path)
    configured = Settings(
        database_url=database_url,
        test_database_url=database_url,
        voice_gateway_service_token=VOICE_TOKEN,
        merchant_admin_token=ADMIN_TOKEN,
        booking_credential_secret=CREDENTIAL_SECRET,
        booking_hold_minutes=5,
        demo_merchant_id=MERCHANT_ID,
        merchant_config_path=base_settings.merchant_config_path,
    )
    probe = DatabaseProbe(database_url)
    with probe.get_engine().begin() as connection:
        # This is the dedicated *_test database. Reset only transient business
        # rows so repeated M3 runs do not consume the same deterministic slots.
        connection.execute(text("TRUNCATE TABLE booking_drafts CASCADE"))
    store = PostgresStore(
        probe.get_engine(),
        booking_hold_minutes=configured.booking_hold_minutes,
        booking_credential_secret=configured.booking_credential_secret,
        web_origin=configured.web_origin,
    )
    with TestClient(create_app(configured, probe, store)) as client:
        yield client, store, configured


def voice_headers(
    owner_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, str]:
    suffix = uuid4().hex
    return {
        "Authorization": f"Bearer {VOICE_TOKEN}",
        "X-Merchant-ID": MERCHANT_ID,
        "X-Voice-Owner-ID": owner_id or f"owner_{suffix}",
        "X-Voice-Session-ID": session_id or f"session_{suffix}",
        "X-Voice-Turn-ID": turn_id or f"turn_{suffix}",
    }


def with_turn(headers: dict[str, str], turn_id: str | None = None) -> dict[str, str]:
    return {**headers, "X-Voice-Turn-ID": turn_id or f"turn_{uuid4().hex}"}


def admin_headers(merchant_id: str = MERCHANT_ID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "X-Merchant-ID": merchant_id,
    }


def build_preparation(
    client: TestClient,
    preferred_date: date,
    *,
    headers: dict[str, str] | None = None,
    customer_name: str = "王女士",
    pet_name: str = "豆包",
) -> tuple[dict[str, str], dict, dict]:
    trusted_headers = headers or voice_headers()
    draft_response = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "customer_name": customer_name,
            "contact_phone": "+86-138-0000-0000",
            "pet_name": pet_name,
            "pet_species": "犬",
            "pet_breed": "贵宾犬",
            "pet_age_years": 3,
            "pet_care_notes": "耳朵敏感，请轻柔处理",
            "need_description": "洗护并检查轻微打结",
            "service_id": "demo-bath",
            "preferred_date": preferred_date.isoformat(),
            "preferred_time_of_day": "morning",
            "staff_preference_text": "Lisa",
            "staff_id": "demo-staff-lisa",
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()["draft"]
    search_response = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": draft["availability_revision"]},
    )
    assert search_response.status_code == 200, search_response.text
    candidate = search_response.json()["search"]["candidates"][0]
    prepare_response = client.post(
        "/api/internal/voice/booking-preparations",
        headers=trusted_headers,
        json={
            "expected_version": draft["version"],
            "candidate_id": candidate["id"],
        },
    )
    assert prepare_response.status_code == 200, prepare_response.text
    return trusted_headers, candidate, prepare_response.json()


def mark_presented(
    client: TestClient,
    headers: dict[str, str],
    prepared: dict,
    *,
    presentation_id: str | None = None,
) -> dict:
    response = client.post(
        f"/api/internal/voice/booking-preparations/{prepared['preparation']['id']}/presented",
        headers={
            **headers,
            "X-Presentation-Event-ID": presentation_id
            or f"presentation_{uuid4().hex}",
        },
        json={"expected_draft_version": prepared["preparation"]["draft_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(
    client: TestClient,
    headers: dict[str, str],
    prepared: dict,
    *,
    confirmation_event_id: str | None = None,
):
    return client.post(
        f"/api/internal/voice/booking-preparations/{prepared['preparation']['id']}/confirm",
        headers={
            **with_turn(headers),
            "X-Confirmation-Event-ID": confirmation_event_id
            or f"confirmation_{uuid4().hex}",
        },
        json={"expected_draft_version": prepared["preparation"]["draft_version"]},
    )


def test_confirmation_gate_idempotency_and_same_record_views(environment) -> None:
    client, store, configured = environment
    headers, _, prepared = build_preparation(client, next_tuesday(70))
    preparation = prepared["preparation"]

    assert prepared["status"] == "prepared"
    assert preparation["availability_held"] is True
    assert preparation["booking_created"] is False
    assert prepared["customer_credential"] is None
    assert preparation["summary"]["source"] == "server_booking_snapshot"

    not_presented = confirm(client, headers, prepared)
    assert not_presented.status_code == 409
    assert not_presented.json()["detail"] == {"code": "confirmation_not_ready"}

    model_supplied_confirmation = client.post(
        f"/api/internal/voice/booking-preparations/{preparation['id']}/confirm",
        headers={
            **with_turn(headers),
            "X-Confirmation-Event-ID": f"confirmation_{uuid4().hex}",
        },
        json={
            "expected_draft_version": preparation["draft_version"],
            "confirmed": True,
        },
    )
    assert model_supplied_confirmation.status_code == 422

    presented = mark_presented(client, headers, prepared)
    assert presented["status"] == "presented"
    assert presented["preparation"]["presentation_id"]

    same_turn = client.post(
        f"/api/internal/voice/booking-preparations/{preparation['id']}/confirm",
        headers={
            **headers,
            "X-Confirmation-Event-ID": f"confirmation_{uuid4().hex}",
        },
        json={"expected_draft_version": preparation["draft_version"]},
    )
    assert same_turn.status_code == 409
    assert same_turn.json()["detail"] == {
        "code": "conflict",
        "reason": "confirmation_requires_new_turn",
    }

    event_id = f"confirmation_{uuid4().hex}"
    confirmed_response = confirm(
        client,
        headers,
        prepared,
        confirmation_event_id=event_id,
    )
    assert confirmed_response.status_code == 200, confirmed_response.text
    confirmed = confirmed_response.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["booking"]["status"] == "confirmed"
    assert confirmed["customer_credential"]["confirmation_code"].startswith("PR-")
    assert "#token=" in confirmed["customer_credential"]["credential_url"]

    retried = confirm(
        client,
        headers,
        prepared,
        confirmation_event_id=f"confirmation_{uuid4().hex}",
    )
    assert retried.status_code == 200
    assert retried.json()["booking"] == confirmed["booking"]
    assert retried.json()["customer_credential"] == confirmed["customer_credential"]

    status_response = client.get(
        f"/api/internal/voice/booking-operations/{confirmed['operation_id']}",
        headers=with_turn(headers),
    )
    assert status_response.status_code == 200
    assert status_response.json()["booking"] == confirmed["booking"]
    assert status_response.json()["customer_credential"] == confirmed["customer_credential"]

    credential = confirmed["customer_credential"]
    customer = client.get(
        f"/api/customer/bookings/{credential['confirmation_code']}",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert customer.status_code == 200
    assert customer.json()["booking"] == confirmed["booking"]
    assert "customer_access_token_hash" not in customer.text
    assert client.get(
        f"/api/customer/bookings/{credential['confirmation_code']}",
        headers={"Authorization": "Bearer wrong-token"},
    ).status_code == 404

    merchant_list = client.get(
        "/api/internal/merchant/bookings",
        headers=admin_headers(),
    )
    assert merchant_list.status_code == 200
    merchant_booking = next(
        booking
        for booking in merchant_list.json()["bookings"]
        if booking["id"] == confirmed["booking"]["id"]
    )
    assert merchant_booking == confirmed["booking"]
    detail = client.get(
        f"/api/internal/merchant/bookings/{confirmed['booking']['id']}",
        headers=admin_headers(),
    )
    assert detail.status_code == 200
    assert detail.json()["booking"] == confirmed["booking"]
    assert client.get(
        "/api/internal/merchant/bookings",
        headers=admin_headers("another-merchant"),
    ).status_code == 403

    immutable = client.patch(
        "/api/internal/voice/booking-draft",
        headers=with_turn(headers),
        json={
            "expected_version": preparation["draft_version"] + 1,
            "contact_phone": "+86-139-0000-0000",
        },
    )
    assert immutable.status_code == 409
    assert immutable.json()["detail"]["code"] == "booking_already_submitted"

    restarted_store = PostgresStore(
        store.engine,
        booking_hold_minutes=5,
        booking_credential_secret=configured.booking_credential_secret,
        web_origin=configured.web_origin,
    )
    restarted_status = restarted_store.get_booking_status(
        VoiceRequestContext(
            merchant_id=MERCHANT_ID,
            owner_id=headers["X-Voice-Owner-ID"],
            voice_session_id=headers["X-Voice-Session-ID"],
            turn_id=f"turn_{uuid4().hex}",
        ),
        UUID(confirmed["operation_id"]),
    )
    assert restarted_status.booking.model_dump(mode="json") == confirmed["booking"]
    assert restarted_status.customer_credential.model_dump(mode="json") == credential


def test_any_draft_change_invalidates_prepared_snapshot(environment) -> None:
    client, _, _ = environment
    headers, _, prepared = build_preparation(client, next_tuesday(84))
    preparation = prepared["preparation"]

    changed = client.patch(
        "/api/internal/voice/booking-draft",
        headers=with_turn(headers),
        json={
            "expected_version": preparation["draft_version"],
            "contact_phone": "+86-136-0000-0000",
        },
    )
    assert changed.status_code == 200
    changed_draft = changed.json()["draft"]
    assert changed_draft["pending_preparation_id"] is None
    assert changed_draft["selected_candidate_id"] is None
    assert changed_draft["availability_revision"] == 1

    rejected = confirm(client, headers, prepared)
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "conflict",
        "reason": "cancelled",
    }

    status_response = client.get(
        f"/api/internal/voice/booking-operations/{prepared['operation_id']}",
        headers=with_turn(headers),
    )
    assert status_response.json()["status"] == "cancelled"
    assert status_response.json()["booking"] is None
    assert status_response.json()["customer_credential"] is None


def test_expired_hold_releases_the_slot(environment) -> None:
    client, store, _ = environment
    target_date = next_tuesday(98)
    headers, first_candidate, prepared = build_preparation(client, target_date)
    original_now_provider = store.now_provider
    expires_at = datetime.fromisoformat(prepared["preparation"]["expires_at"])
    store.now_provider = lambda zone: (expires_at + timedelta(seconds=1)).astimezone(zone)
    try:
        expired = client.get(
            f"/api/internal/voice/booking-operations/{prepared['operation_id']}",
            headers=with_turn(headers),
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "expired"
        assert expired.json()["preparation"]["availability_held"] is False

        second_headers = voice_headers()
        _, candidate, second_prepared = build_preparation(
            client,
            target_date,
            headers=second_headers,
            customer_name="赵先生",
            pet_name="团子",
        )
        assert candidate["starts_at"] == first_candidate["starts_at"]
        assert second_prepared["status"] == "prepared"
    finally:
        store.now_provider = original_now_provider


def test_competing_sessions_create_only_one_hold(environment) -> None:
    client, store, configured = environment
    target_date = next_tuesday(112)
    first_headers = voice_headers()
    second_headers = voice_headers()

    def draft_and_candidate(headers: dict[str, str], customer_name: str) -> tuple[VoiceRequestContext, int, UUID]:
        draft_response = client.patch(
            "/api/internal/voice/booking-draft",
            headers=headers,
            json={
                "expected_version": 0,
                "customer_name": customer_name,
                "contact_phone": "+86-138-1111-1111",
                "pet_name": f"{customer_name}的宠物",
                "pet_species": "犬",
                "service_id": "demo-bath",
                "preferred_date": target_date.isoformat(),
                "preferred_time_of_day": "morning",
                "staff_id": "demo-staff-lisa",
            },
        )
        draft = draft_response.json()["draft"]
        search = client.post(
            "/api/internal/voice/availability-searches",
            headers=headers,
            json={"expected_availability_revision": draft["availability_revision"]},
        ).json()["search"]
        return (
            VoiceRequestContext(
                merchant_id=MERCHANT_ID,
                owner_id=headers["X-Voice-Owner-ID"],
                voice_session_id=headers["X-Voice-Session-ID"],
                turn_id=headers["X-Voice-Turn-ID"],
            ),
            draft["version"],
            UUID(search["candidates"][0]["id"]),
        )

    first = draft_and_candidate(first_headers, "并发甲")
    second = draft_and_candidate(second_headers, "并发乙")
    assert first[2] != second[2]

    def run_prepare(arguments):
        context, version, candidate_id = arguments
        local_store = PostgresStore(
            store.engine,
            booking_hold_minutes=5,
            booking_credential_secret=configured.booking_credential_secret,
            web_origin=configured.web_origin,
        )
        try:
            return local_store.prepare_booking(context, version, candidate_id)
        except Exception as error:  # Asserted in the parent thread.
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_prepare, (first, second)))

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CandidateUnavailableError)
    assert str(failures[0]) == "candidate_conflict"


def test_concurrent_draft_change_and_confirmation_cannot_commit_both(environment) -> None:
    client, store, configured = environment
    headers, _, prepared = build_preparation(client, next_tuesday(140))
    mark_presented(client, headers, prepared)
    prepared_version = prepared["preparation"]["draft_version"]
    preparation_id = UUID(prepared["preparation"]["id"])
    barrier = Barrier(2)

    def local_store() -> PostgresStore:
        return PostgresStore(
            store.engine,
            booking_hold_minutes=5,
            booking_credential_secret=configured.booking_credential_secret,
            web_origin=configured.web_origin,
        )

    def update_draft():
        barrier.wait(timeout=5)
        try:
            return local_store().update_draft(
                VoiceRequestContext(
                    merchant_id=MERCHANT_ID,
                    owner_id=headers["X-Voice-Owner-ID"],
                    voice_session_id=headers["X-Voice-Session-ID"],
                    turn_id=f"turn_{uuid4().hex}",
                ),
                prepared_version,
                {"contact_phone": "+86-137-0000-0000"},
            )
        except Exception as error:  # Asserted in the parent thread.
            return error

    def confirm_booking():
        barrier.wait(timeout=5)
        try:
            return local_store().confirm_booking(
                VoiceRequestContext(
                    merchant_id=MERCHANT_ID,
                    owner_id=headers["X-Voice-Owner-ID"],
                    voice_session_id=headers["X-Voice-Session-ID"],
                    turn_id=f"turn_{uuid4().hex}",
                ),
                preparation_id,
                prepared_version,
                f"confirmation_{uuid4().hex}",
            )
        except Exception as error:  # Asserted in the parent thread.
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        update_future = executor.submit(update_draft)
        confirm_future = executor.submit(confirm_booking)
        update_result = update_future.result(timeout=10)
        confirm_result = confirm_future.result(timeout=10)

    if isinstance(update_result, Exception):
        assert isinstance(update_result, BookingAlreadySubmittedError)
        assert not isinstance(confirm_result, Exception)
        assert confirm_result.status == "confirmed"
    else:
        assert isinstance(confirm_result, BookingConfirmationConflictError)
        assert str(confirm_result) in {"cancelled", "draft_version_changed"}
        assert update_result[0].contact_phone == "+86-137-0000-0000"


def test_database_insert_failure_rolls_back_and_returns_no_credential(environment) -> None:
    client, store, configured = environment
    headers, _, prepared = build_preparation(client, next_tuesday(126))
    mark_presented(client, headers, prepared)
    preparation_id = UUID(prepared["preparation"]["id"])

    with store.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION m3_test_reject_booking_insert()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'm3 injected booking write failure';
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER m3_test_reject_booking_insert_trigger "
                "BEFORE INSERT ON bookings FOR EACH ROW "
                "EXECUTE FUNCTION m3_test_reject_booking_insert()"
            )
        )

    failure_probe = DatabaseProbe(configured.database_url)
    failure_store = PostgresStore(
        failure_probe.get_engine(),
        booking_hold_minutes=5,
        booking_credential_secret=configured.booking_credential_secret,
        web_origin=configured.web_origin,
    )
    try:
        with TestClient(
            create_app(configured, failure_probe, failure_store),
            raise_server_exceptions=False,
        ) as failure_client:
            response = confirm(failure_client, headers, prepared)
            assert response.status_code == 500
            assert "access_token" not in response.text
            assert "confirmation_code" not in response.text
    finally:
        with store.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER IF EXISTS m3_test_reject_booking_insert_trigger ON bookings")
            )
            connection.execute(text("DROP FUNCTION IF EXISTS m3_test_reject_booking_insert()"))

    with store.engine.connect() as connection:
        booking_count = connection.execute(
            select(func.count()).select_from(bookings).where(
                bookings.c.preparation_id == preparation_id
            )
        ).scalar_one()
        preparation_status = connection.execute(
            select(booking_preparations.c.status).where(
                booking_preparations.c.id == preparation_id
            )
        ).scalar_one()
    assert booking_count == 0
    assert preparation_status == "presented"
