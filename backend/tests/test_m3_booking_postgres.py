import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier, Event
from time import sleep
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.models import (
    booking_drafts,
    booking_preparations,
    bookings,
    merchants,
)
from pet_reception_backend.schemas import VoiceRequestContext
from pet_reception_backend.store import (
    BookingAlreadySubmittedError,
    BookingConfirmationConflictError,
    BookingPreparationExpiredError,
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
    legacy_config_path = "config/demo-merchant.json"
    setup_database(database_url, legacy_config_path)
    configured = Settings(
        database_url=database_url,
        test_database_url=database_url,
        voice_gateway_service_token=VOICE_TOKEN,
        merchant_admin_token=ADMIN_TOKEN,
        booking_credential_secret=CREDENTIAL_SECRET,
        booking_hold_minutes=5,
        demo_merchant_id=MERCHANT_ID,
        merchant_config_path=legacy_config_path,
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


def build_search(
    client: TestClient,
    preferred_date: date,
    *,
    headers: dict[str, str] | None = None,
    service_id: str = "demo-bath",
    preferred_time_of_day: str = "morning",
    customer_name: str = "Review 客户",
) -> tuple[dict[str, str], dict, dict]:
    trusted_headers = headers or voice_headers()
    draft_response = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "customer_name": customer_name,
            "contact_phone": "+86-138-2222-2222",
            "pet_name": f"{customer_name}的宠物",
            "pet_species": "犬",
            "service_id": service_id,
            "preferred_date": preferred_date.isoformat(),
            "preferred_time_of_day": preferred_time_of_day,
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
    return trusted_headers, draft, search_response.json()["search"]


def prepare_candidate(
    client: TestClient,
    headers: dict[str, str],
    draft: dict,
    candidate: dict,
) -> dict:
    response = client.post(
        "/api/internal/voice/booking-preparations",
        headers=headers,
        json={
            "expected_version": draft["version"],
            "candidate_id": candidate["id"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm_prepared(
    client: TestClient,
    headers: dict[str, str],
    prepared: dict,
) -> dict:
    mark_presented(client, headers, prepared)
    response = confirm(client, headers, prepared)
    assert response.status_code == 200, response.text
    return response.json()


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
    awaiting = confirmed_response.json()
    assert awaiting["status"] == "awaiting_payment"
    assert awaiting["booking"]["status"] == "awaiting_payment"
    assert awaiting["booking"]["confirmation_code"] is None
    assert awaiting["customer_credential"]["confirmation_code"] is None
    assert awaiting["customer_credential"]["booking_reference"].startswith("BK-")
    assert "#token=" in awaiting["customer_credential"]["credential_url"]

    retried = confirm(
        client,
        headers,
        prepared,
        confirmation_event_id=f"confirmation_{uuid4().hex}",
    )
    assert retried.status_code == 200
    assert retried.json()["booking"] == awaiting["booking"]
    assert retried.json()["customer_credential"] == awaiting["customer_credential"]


    credential = awaiting["customer_credential"]
    customer_pending = client.get(
        f"/api/customer/bookings/{credential['booking_reference']}",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert customer_pending.status_code == 200
    assert customer_pending.json()["booking"] == awaiting["booking"]

    payment_event_id = f"payment_{uuid4().hex}"
    paid_response = client.post(
        f"/api/customer/bookings/{credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {credential['access_token']}",
            "X-Payment-Event-ID": payment_event_id,
        },
    )
    assert paid_response.status_code == 200, paid_response.text
    confirmed_booking = paid_response.json()["booking"]
    assert confirmed_booking["status"] == "confirmed"
    assert confirmed_booking["confirmation_code"].startswith("PR-")
    assert confirmed_booking["payment_method"] == "local_simulation"
    assert confirmed_booking["paid_at"] is not None

    paid_retry = client.post(
        f"/api/customer/bookings/{credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {credential['access_token']}",
            "X-Payment-Event-ID": f"payment_{uuid4().hex}",
        },
    )
    assert paid_retry.status_code == 200
    assert paid_retry.json()["booking"] == confirmed_booking

    confirmed = {
        **awaiting,
        "status": "confirmed",
        "booking": confirmed_booking,
        "customer_credential": {
            **credential,
            "confirmation_code": confirmed_booking["confirmation_code"],
        },
    }

    status_response = client.get(
        f"/api/internal/voice/booking-operations/{confirmed['operation_id']}",
        headers=with_turn(headers),
    )
    assert status_response.status_code == 200
    assert status_response.json()["booking"] == confirmed["booking"]
    assert status_response.json()["customer_credential"] == confirmed["customer_credential"]

    preparation_response = client.get(
        f"/api/internal/voice/booking-preparations/{preparation['id']}",
        headers=with_turn(headers),
    )
    assert preparation_response.status_code == 200
    assert preparation_response.json()["status"] == "confirmed"
    assert preparation_response.json()["booking"] == confirmed["booking"]

    customer = client.get(
        f"/api/customer/bookings/{credential['booking_reference']}",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert customer.status_code == 200
    assert customer.json()["booking"] == confirmed["booking"]
    assert "customer_access_token_hash" not in customer.text
    assert client.get(
        f"/api/customer/bookings/{credential['booking_reference']}",
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
    submitted_detail = immutable.json()["detail"]
    assert submitted_detail["code"] == "booking_already_submitted"
    assert submitted_detail["operation_id"] == confirmed["operation_id"]
    assert submitted_detail["status"] == "confirmed"
    assert submitted_detail["booking_reference"] == credential["booking_reference"]
    assert submitted_detail["recovery"]["action"] == "use_start_new_booking_control"

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
    assert (
        restarted_status.customer_credential.model_dump(mode="json")
        == confirmed["customer_credential"]
    )


def test_presented_preparation_is_recoverable_by_identity_after_store_restart(
    environment,
) -> None:
    client, store, configured = environment
    headers, _, prepared = build_preparation(client, next_tuesday(280))
    presentation_id = f"presentation_{uuid4().hex}"
    presented = mark_presented(
        client,
        headers,
        prepared,
        presentation_id=presentation_id,
    )

    response = client.get(
        f"/api/internal/voice/booking-preparations/{prepared['preparation']['id']}",
        headers=with_turn(headers),
    )
    assert response.status_code == 200, response.text
    recovered = response.json()
    assert recovered["operation_id"] == prepared["operation_id"]
    assert recovered["status"] == "presented"
    assert recovered["preparation"]["presentation_id"] == presentation_id
    assert recovered["preparation"]["summary"] == presented["preparation"]["summary"]

    attacker = client.get(
        f"/api/internal/voice/booking-preparations/{prepared['preparation']['id']}",
        headers=voice_headers(),
    )
    assert attacker.status_code == 404

    restarted_store = PostgresStore(
        store.engine,
        booking_hold_minutes=configured.booking_hold_minutes,
        payment_hold_minutes=configured.payment_hold_minutes,
        booking_credential_secret=configured.booking_credential_secret,
        web_origin=configured.web_origin,
    )
    context = VoiceRequestContext(
        merchant_id=MERCHANT_ID,
        owner_id=headers["X-Voice-Owner-ID"],
        voice_session_id=headers["X-Voice-Session-ID"],
        turn_id=f"turn_{uuid4().hex}",
    )
    recovered_from_new_store = restarted_store.get_booking_preparation(
        context,
        UUID(prepared["preparation"]["id"]),
    )
    assert recovered_from_new_store.status == "presented"
    assert recovered_from_new_store.preparation is not None
    assert recovered_from_new_store.preparation.presentation_id == presentation_id


def test_paid_booking_can_start_a_second_trusted_session_without_reusing_old_draft(
    environment,
) -> None:
    client, store, _ = environment
    owner_id = f"owner_{uuid4().hex}"
    first_headers = voice_headers(owner_id=owner_id)
    first_headers, _, first_prepared = build_preparation(
        client, next_tuesday(196), headers=first_headers, pet_name="第一只"
    )
    first_awaiting = confirm_prepared(client, first_headers, first_prepared)
    first_credential = first_awaiting["customer_credential"]
    first_paid = client.post(
        f"/api/customer/bookings/{first_credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {first_credential['access_token']}",
            "X-Payment-Event-ID": f"payment_{uuid4().hex}",
        },
    )
    assert first_paid.status_code == 200, first_paid.text
    assert first_paid.json()["booking"]["status"] == "confirmed"

    first_terminal = client.post(
        "/api/internal/voice/availability-searches",
        headers=with_turn(first_headers),
        json={"expected_availability_revision": 1},
    )
    assert first_terminal.status_code == 409
    terminal_detail = first_terminal.json()["detail"]
    assert terminal_detail["operation_id"] == first_awaiting["operation_id"]
    assert terminal_detail["status"] == "confirmed"

    second_session_id = f"booking-{uuid4()}"
    second_headers = voice_headers(owner_id=owner_id, session_id=second_session_id)
    started = client.post(
        "/api/internal/voice/booking-sessions/start",
        headers=second_headers,
        json={"previous_voice_session_id": first_headers["X-Voice-Session-ID"]},
    )
    assert started.status_code == 201, started.text
    blank = started.json()["draft"]
    assert blank["version"] == 0
    assert blank["booking_id"] is None
    assert blank["preferred_date"] is None

    second_date = next_tuesday(210)
    second_update = client.patch(
        "/api/internal/voice/booking-draft",
        headers=second_headers,
        json={
            "expected_version": 0,
            "customer_name": "雷女士",
            "contact_phone": "+86-138-3333-4444",
            "pet_name": "金毛",
            "pet_species": "狗狗",
            "pet_age_text": "四个月",
            "service_id": "demo-bath",
            "preferred_date": second_date.isoformat(),
            "preferred_time_of_day": "afternoon",
            "staff_id": "demo-staff-lisa",
        },
    )
    assert second_update.status_code == 200, second_update.text
    second_draft = second_update.json()["draft"]
    assert second_draft["pet_name"] == "金毛"
    assert second_draft["pet_species"] == "犬"
    assert second_draft["pet_age_text"] == "四个月"
    assert second_draft["pet_age_months"] == 4
    assert second_draft["pet_age_years"] is None

    stale_old_request = client.patch(
        "/api/internal/voice/booking-draft",
        headers=with_turn(first_headers),
        json={
            "expected_version": first_prepared["preparation"]["draft_version"] + 1,
            "customer_name": "不应进入新草稿",
        },
    )
    assert stale_old_request.status_code == 409
    reread_second = client.get(
        "/api/internal/voice/booking-draft", headers=with_turn(second_headers)
    ).json()["draft"]
    assert reread_second["customer_name"] == "雷女士"

    second_search = client.post(
        "/api/internal/voice/availability-searches",
        headers=second_headers,
        json={
            "expected_availability_revision": second_draft["availability_revision"]
        },
    )
    assert second_search.status_code == 200, second_search.text
    second_candidate = second_search.json()["search"]["candidates"][0]
    second_prepared_response = client.post(
        "/api/internal/voice/booking-preparations",
        headers=second_headers,
        json={
            "expected_version": second_draft["version"],
            "candidate_id": second_candidate["id"],
        },
    )
    assert second_prepared_response.status_code == 200, second_prepared_response.text
    second_prepared = second_prepared_response.json()
    assert second_prepared["preparation"]["summary"]["pet_age_text"] == "四个月"
    second_awaiting = confirm_prepared(client, second_headers, second_prepared)
    assert second_awaiting["status"] == "awaiting_payment"

    restarted = PostgresStore(
        store.engine,
        booking_credential_secret=CREDENTIAL_SECRET,
    )
    restored = restarted.get_booking_status(
        VoiceRequestContext(
            merchant_id=MERCHANT_ID,
            owner_id=owner_id,
            voice_session_id=second_session_id,
            turn_id=f"turn_{uuid4().hex}",
        ),
        UUID(second_awaiting["operation_id"]),
    )
    assert restored.status == "awaiting_payment"

    third_headers = voice_headers(
        owner_id=owner_id, session_id=f"booking-{uuid4()}"
    )
    third_started = client.post(
        "/api/internal/voice/booking-sessions/start",
        headers=third_headers,
        json={"previous_voice_session_id": second_session_id},
    )
    assert third_started.status_code == 201
    third_update = client.patch(
        "/api/internal/voice/booking-draft",
        headers=third_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-bath",
            "preferred_time_of_day": "afternoon",
        },
    )
    assert third_update.status_code == 200
    no_date = client.post(
        "/api/internal/voice/availability-searches",
        headers=third_headers,
        json={
            "expected_availability_revision": third_update.json()["draft"][
                "availability_revision"
            ]
        },
    )
    assert no_date.status_code == 422
    assert no_date.json()["detail"] == {
        "code": "needs_input",
        "missing_fields": ["preferred_date"],
    }
    assert third_update.json()["draft"]["preferred_date"] is None


def test_prepare_and_confirm_revalidate_legacy_invalid_draft_fields(environment) -> None:
    client, store, _ = environment
    headers, draft, search = build_search(client, next_tuesday(224))
    candidate = search["candidates"][0]
    with store.engine.begin() as connection:
        connection.execute(
            booking_drafts.update()
            .where(booking_drafts.c.id == UUID(draft["id"]))
            .values(contact_phone="1234567abc", pet_species="猫")
        )
    rejected_prepare = client.post(
        "/api/internal/voice/booking-preparations",
        headers=headers,
        json={
            "expected_version": draft["version"],
            "candidate_id": candidate["id"],
        },
    )
    assert rejected_prepare.status_code == 422
    assert rejected_prepare.json()["detail"] == {
        "code": "invalid_booking_fields",
        "invalid_fields": {
            "contact_phone": "invalid_contact_phone",
            "pet_species": "unsupported_for_service",
        },
    }

    confirm_headers, _, prepared = build_preparation(client, next_tuesday(238))
    mark_presented(client, confirm_headers, prepared)
    with store.engine.begin() as connection:
        connection.execute(
            booking_drafts.update()
            .where(
                booking_drafts.c.id
                == UUID(prepared["preparation"]["draft_id"])
            )
            .values(contact_phone="incoming_phone_13800000000")
        )
    rejected_confirm = confirm(client, confirm_headers, prepared)
    assert rejected_confirm.status_code == 422
    assert rejected_confirm.json()["detail"]["code"] == "invalid_booking_fields"
    assert rejected_confirm.json()["detail"]["invalid_fields"] == {
        "contact_phone": "invalid_contact_phone"
    }


def test_payment_expiry_releases_draft_and_rejects_late_payment(environment) -> None:
    client, store, _ = environment
    preferred_date = next_tuesday(77)
    headers, candidate, prepared = build_preparation(client, preferred_date)
    awaiting = confirm_prepared(client, headers, prepared)
    credential = awaiting["customer_credential"]
    booking_id = UUID(awaiting["booking"]["id"])

    with store.engine.begin() as connection:
        connection.execute(
            bookings.update()
            .where(bookings.c.id == booking_id)
            .values(payment_expires_at=datetime.now(ZoneInfo("UTC")) - timedelta(seconds=1))
        )

    late_payment = client.post(
        f"/api/customer/bookings/{credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {credential['access_token']}",
            "X-Payment-Event-ID": f"payment_{uuid4().hex}",
        },
    )
    assert late_payment.status_code == 409
    assert late_payment.json()["detail"]["code"] == "payment_expired"

    customer = client.get(
        f"/api/customer/bookings/{credential['booking_reference']}",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert customer.status_code == 200
    assert customer.json()["booking"]["status"] == "payment_expired"
    assert customer.json()["booking"]["confirmation_code"] is None

    with store.engine.connect() as connection:
        draft = connection.execute(
            select(booking_drafts).where(
                booking_drafts.c.merchant_id == MERCHANT_ID,
                booking_drafts.c.owner_id == headers["X-Voice-Owner-ID"],
                booking_drafts.c.voice_session_id == headers["X-Voice-Session-ID"],
            )
        ).mappings().one()
    assert draft["booking_id"] is None
    assert draft["selected_candidate_id"] is None

    _, _, refreshed_search = build_search(client, preferred_date)
    assert any(
        value["starts_at"] == candidate["starts_at"]
        and value["staff_id"] == candidate["staff_id"]
        for value in refreshed_search["candidates"]
    )


def test_payment_event_cannot_be_reused_for_another_booking(environment) -> None:
    client, _, _ = environment
    first_headers, _, first_prepared = build_preparation(client, next_tuesday(84))
    second_headers, _, second_prepared = build_preparation(client, next_tuesday(91))
    first = confirm_prepared(client, first_headers, first_prepared)
    second = confirm_prepared(client, second_headers, second_prepared)
    event_id = f"payment_{uuid4().hex}"

    first_credential = first["customer_credential"]
    assert client.post(
        f"/api/customer/bookings/{first_credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {first_credential['access_token']}",
            "X-Payment-Event-ID": event_id,
        },
    ).status_code == 200

    second_credential = second["customer_credential"]
    reused = client.post(
        f"/api/customer/bookings/{second_credential['booking_reference']}/simulate-payment",
        headers={
            "Authorization": f"Bearer {second_credential['access_token']}",
            "X-Payment-Event-ID": event_id,
        },
    )
    assert reused.status_code == 409
    assert reused.json()["detail"] == {
        "code": "payment_conflict",
        "reason": "payment_event_reused",
    }


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
    recovered = client.get(
        f"/api/internal/voice/booking-preparations/{preparation['id']}",
        headers=with_turn(headers),
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "cancelled"
    assert recovered.json()["booking"] is None


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
        recovered = client.get(
            f"/api/internal/voice/booking-preparations/{prepared['preparation']['id']}",
            headers=with_turn(headers),
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "expired"
        assert recovered.json()["booking"] is None

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
        assert confirm_result.status == "awaiting_payment"
    else:
        assert isinstance(confirm_result, BookingConfirmationConflictError)
        assert str(confirm_result) in {"cancelled", "draft_version_changed"}
        assert update_result[0].contact_phone == "+86-137-0000-0000"


def test_availability_save_locks_merchant_before_draft(environment) -> None:
    _, store, _ = environment
    trusted_context = VoiceRequestContext(
        merchant_id=MERCHANT_ID,
        owner_id=f"owner_{uuid4().hex}",
        voice_session_id=f"session_{uuid4().hex}",
        turn_id=f"turn_{uuid4().hex}",
    )
    initial = store.get_or_create_draft(trusted_context)
    draft, _ = store.update_draft(
        trusted_context,
        initial.version,
        {
            "service_id": "demo-bath",
            "preferred_date": next_tuesday(154),
            "staff_id": "demo-staff-lisa",
        },
    )
    lock_statements: list[str] = []

    def record_lock_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = statement.lower()
        if "for update" in normalized:
            lock_statements.append(normalized)

    event.listen(store.engine, "before_cursor_execute", record_lock_statement)
    try:
        store.search_availability(trusted_context, draft.availability_revision)
    finally:
        event.remove(store.engine, "before_cursor_execute", record_lock_statement)

    merchant_lock = next(
        index
        for index, statement in enumerate(lock_statements)
        if "from merchants" in statement
    )
    draft_lock = next(
        index
        for index, statement in enumerate(lock_statements)
        if "from booking_drafts" in statement
    )
    assert merchant_lock < draft_lock


def test_saved_search_revalidates_holds_bookings_and_alternatives(environment) -> None:
    client, store, _ = environment
    held_date = next_tuesday(168)
    waiting_headers, _, waiting_search = build_search(
        client,
        held_date,
        customer_name="等待占位客户",
    )
    holder_headers, holder_draft, holder_search = build_search(
        client,
        held_date,
        customer_name="占位客户",
    )
    held_start = waiting_search["candidates"][0]["starts_at"]
    held = prepare_candidate(
        client,
        holder_headers,
        holder_draft,
        holder_search["candidates"][0],
    )

    reread_while_held = client.get(
        f"/api/internal/voice/availability-searches/{waiting_search['id']}",
        headers=waiting_headers,
    ).json()["search"]
    assert all(
        candidate["starts_at"] != held_start
        for candidate in reread_while_held["candidates"]
    )

    original_now_provider = store.now_provider
    expires_at = datetime.fromisoformat(held["preparation"]["expires_at"])
    store.now_provider = lambda zone: (expires_at + timedelta(seconds=1)).astimezone(zone)
    try:
        reread_after_expiry = client.get(
            f"/api/internal/voice/availability-searches/{waiting_search['id']}",
            headers=waiting_headers,
        ).json()["search"]
        assert any(
            candidate["starts_at"] == held_start
            for candidate in reread_after_expiry["candidates"]
        )
    finally:
        store.now_provider = original_now_provider

    booked_date = next_tuesday(182)
    waiting_headers, _, waiting_search = build_search(
        client,
        booked_date,
        customer_name="等待预约客户",
    )
    booker_headers, booker_draft, booker_search = build_search(
        client,
        booked_date,
        customer_name="预约客户",
    )
    booked_start = waiting_search["candidates"][0]["starts_at"]
    booked_preparation = prepare_candidate(
        client,
        booker_headers,
        booker_draft,
        booker_search["candidates"][0],
    )
    confirm_prepared(client, booker_headers, booked_preparation)
    reread_after_booking = client.get(
        f"/api/internal/voice/availability-searches/{waiting_search['id']}",
        headers=waiting_headers,
    ).json()["search"]
    assert all(
        candidate["starts_at"] != booked_start
        for candidate in reread_after_booking["candidates"]
    )

    start = datetime.now(MERCHANT_ZONE).date() + timedelta(days=196)
    closed_sunday = start + timedelta(days=(6 - start.weekday()) % 7)
    alternative_headers, _, alternative_search = build_search(
        client,
        closed_sunday,
        service_id="demo-full-groom",
        preferred_time_of_day="flexible",
        customer_name="替代日期客户",
    )
    assert alternative_search["status"] == "no_availability"
    first_alternative = alternative_search["alternative_dates"][0]
    holder_headers, holder_draft, holder_search = build_search(
        client,
        date.fromisoformat(first_alternative["date"]),
        service_id="demo-full-groom",
        preferred_time_of_day="flexible",
        customer_name="替代日期占位客户",
    )
    alternative_hold = prepare_candidate(
        client,
        holder_headers,
        holder_draft,
        holder_search["candidates"][0],
    )
    refreshed_alternatives = client.get(
        f"/api/internal/voice/availability-searches/{alternative_search['id']}",
        headers=alternative_headers,
    ).json()["search"]["alternative_dates"]
    assert not any(
        alternative["first_start_at"] == first_alternative["first_start_at"]
        and "demo-staff-lisa" in alternative["available_staff_ids"]
        for alternative in refreshed_alternatives
    )
    assert alternative_hold["status"] == "prepared"


@pytest.mark.parametrize("conflict_kind", ["bookings", "holds"])
def test_long_candidate_rejects_two_overlapping_conflicts(
    environment,
    conflict_kind: str,
) -> None:
    client, _, _ = environment
    days = 210 if conflict_kind == "bookings" else 224
    target_date = next_tuesday(days)
    long_headers, long_draft, long_search = build_search(
        client,
        target_date,
        service_id="demo-full-groom",
        customer_name=f"长服务-{conflict_kind}",
    )
    long_candidate = long_search["candidates"][0]

    first_headers, first_draft, first_search = build_search(
        client,
        target_date,
        customer_name=f"短服务甲-{conflict_kind}",
    )
    first_prepared = prepare_candidate(
        client,
        first_headers,
        first_draft,
        first_search["candidates"][0],
    )
    if conflict_kind == "bookings":
        confirm_prepared(client, first_headers, first_prepared)

    second_headers, second_draft, second_search = build_search(
        client,
        target_date,
        customer_name=f"短服务乙-{conflict_kind}",
    )
    overlapping_candidates = [
        candidate
        for candidate in second_search["candidates"]
        if candidate["busy_starts_at"] < long_candidate["busy_ends_at"]
        and candidate["busy_ends_at"] > long_candidate["busy_starts_at"]
    ]
    assert overlapping_candidates
    second_prepared = prepare_candidate(
        client,
        second_headers,
        second_draft,
        overlapping_candidates[0],
    )
    if conflict_kind == "bookings":
        confirm_prepared(client, second_headers, second_prepared)

    rejected = client.post(
        "/api/internal/voice/booking-preparations",
        headers=long_headers,
        json={
            "expected_version": long_draft["version"],
            "candidate_id": long_candidate["id"],
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "unavailable",
        "reason": "candidate_conflict",
    }


def test_bulk_and_direct_expiry_clear_draft_references(environment) -> None:
    client, store, _ = environment
    target_date = next_tuesday(238)
    first_headers, first_candidate, first_prepared = build_preparation(
        client,
        target_date,
        customer_name="批量过期甲",
    )
    expires_at = datetime.fromisoformat(first_prepared["preparation"]["expires_at"])
    original_now_provider = store.now_provider
    store.now_provider = lambda zone: (expires_at + timedelta(seconds=1)).astimezone(zone)
    try:
        _, second_candidate, second_prepared = build_preparation(
            client,
            target_date,
            customer_name="批量过期乙",
        )
        assert second_candidate["starts_at"] == first_candidate["starts_at"]
        assert second_prepared["status"] == "prepared"
        first_draft = client.get(
            "/api/internal/voice/booking-draft",
            headers=with_turn(first_headers),
        ).json()["draft"]
        assert first_draft["pending_preparation_id"] is None
        assert first_draft["selected_candidate_id"] is None
    finally:
        store.now_provider = original_now_provider

    direct_headers, _, direct_prepared = build_preparation(
        client,
        next_tuesday(252),
        customer_name="直接过期客户",
    )
    mark_presented(client, direct_headers, direct_prepared)
    direct_id = UUID(direct_prepared["preparation"]["id"])
    direct_expiry = datetime.fromisoformat(
        direct_prepared["preparation"]["expires_at"]
    )
    store.now_provider = lambda zone: (direct_expiry + timedelta(seconds=1)).astimezone(zone)
    try:
        rejected = confirm(client, direct_headers, direct_prepared)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "expired"
    finally:
        store.now_provider = original_now_provider
    with store.engine.connect() as connection:
        status = connection.execute(
            select(booking_preparations.c.status).where(
                booking_preparations.c.id == direct_id
            )
        ).scalar_one()
    direct_draft = client.get(
        "/api/internal/voice/booking-draft",
        headers=with_turn(direct_headers),
    ).json()["draft"]
    assert status == "expired"
    assert direct_draft["pending_preparation_id"] is None
    assert direct_draft["selected_candidate_id"] is None


def test_confirmation_rechecks_expiry_after_lock_wait(environment) -> None:
    client, store, configured = environment
    headers, _, prepared = build_preparation(
        client,
        next_tuesday(266),
        customer_name="锁等待过期客户",
    )
    mark_presented(client, headers, prepared)
    preparation_id = UUID(prepared["preparation"]["id"])
    base_now = datetime.now(ZoneInfo("UTC"))
    clock = {"now": base_now}
    with store.engine.begin() as connection:
        connection.execute(
            booking_preparations.update()
            .where(booking_preparations.c.id == preparation_id)
            .values(expires_at=base_now + timedelta(seconds=1))
        )

    waiting_store = PostgresStore(
        store.engine,
        now_provider=lambda zone: clock["now"].astimezone(zone),
        booking_hold_minutes=5,
        booking_credential_secret=configured.booking_credential_secret,
        web_origin=configured.web_origin,
    )
    trusted_context = VoiceRequestContext(
        merchant_id=MERCHANT_ID,
        owner_id=headers["X-Voice-Owner-ID"],
        voice_session_id=headers["X-Voice-Session-ID"],
        turn_id=f"turn_{uuid4().hex}",
    )
    started = Event()

    def confirm_while_waiting():
        started.set()
        try:
            return waiting_store.confirm_booking(
                trusted_context,
                preparation_id,
                prepared["preparation"]["draft_version"],
                f"confirmation_{uuid4().hex}",
            )
        except Exception as error:  # Asserted in the parent thread.
            return error

    blocker = store.engine.connect()
    transaction = blocker.begin()
    blocker.execute(
        select(merchants.c.id)
        .where(merchants.c.id == MERCHANT_ID)
        .with_for_update()
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(confirm_while_waiting)
            assert started.wait(timeout=5)
            sleep(0.25)
            assert not future.done()
            clock["now"] = base_now + timedelta(seconds=2)
            transaction.commit()
            result = future.result(timeout=10)
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()

    assert isinstance(result, BookingPreparationExpiredError)
    with store.engine.connect() as connection:
        assert connection.execute(
            select(func.count()).select_from(bookings).where(
                bookings.c.preparation_id == preparation_id
            )
        ).scalar_one() == 0
        assert connection.execute(
            select(booking_preparations.c.status).where(
                booking_preparations.c.id == preparation_id
            )
        ).scalar_one() == "expired"


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
