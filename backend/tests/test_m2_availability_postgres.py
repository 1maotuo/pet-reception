import os
from datetime import date, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from pet_reception_backend.app import create_app
from pet_reception_backend.availability import candidate_windows_for_date
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.merchant_catalog import load_demo_merchant
from pet_reception_backend.schemas import VoiceRequestContext
from pet_reception_backend.store import (
    AvailabilityRevisionConflictError,
    PostgresStore,
)


pytestmark = pytest.mark.postgres


def next_weekday(start: date, weekday: int) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7)


MERCHANT_TODAY = datetime.now(ZoneInfo("America/Los_Angeles")).date()
OPEN_FRIDAY = next_weekday(MERCHANT_TODAY + timedelta(days=14), 4)
CLOSED_SUNDAY = next_weekday(OPEN_FRIDAY + timedelta(days=1), 6)
NEXT_MONDAY = CLOSED_SUNDAY + timedelta(days=1)


@pytest.fixture(scope="module")
def environment():
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise M2 availability")

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
    with TestClient(create_app(configured, probe, store)) as client:
        yield client, store


def headers(owner_id: str | None = None, session_id: str | None = None) -> dict[str, str]:
    suffix = uuid4().hex
    return {
        "Authorization": "Bearer m2-test-service-token",
        "X-Merchant-ID": "demo-grooming-001",
        "X-Voice-Owner-ID": owner_id or f"owner_{suffix}",
        "X-Voice-Session-ID": session_id or f"session_{suffix}",
        "X-Voice-Turn-ID": f"turn_{suffix}",
    }


def context() -> VoiceRequestContext:
    suffix = uuid4().hex
    return VoiceRequestContext(
        merchant_id="demo-grooming-001",
        owner_id=f"owner_{suffix}",
        voice_session_id=f"session_{suffix}",
        turn_id=f"turn_{suffix}",
    )


def test_merchant_exposes_only_real_seeded_staff(environment) -> None:
    client, _ = environment
    response = client.get("/api/internal/voice/merchant-info", headers=headers())

    assert response.status_code == 200
    staff = response.json()["merchant"]["staff"]
    assert staff == [
        {
            "id": "demo-staff-lisa",
            "display_name": "Lisa",
            "service_ids": ["demo-bath", "demo-full-groom"],
            "source": "m2-demo-schedule:demo-staff-lisa",
        },
        {
            "id": "demo-staff-marco",
            "display_name": "Marco",
            "service_ids": ["demo-bath"],
            "source": "m2-demo-schedule:demo-staff-marco",
        },
    ]


def test_search_requires_only_real_availability_inputs(environment) -> None:
    client, _ = environment
    trusted_headers = headers()

    missing = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": 0},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"] == {
        "code": "needs_input",
        "missing_fields": ["service_id", "preferred_date"],
    }

    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-bath",
            "preferred_date": OPEN_FRIDAY.isoformat(),
            "staff_preference_text": "Lisa",
        },
    )
    assert draft.status_code == 200
    assert draft.json()["missing_for_availability"] == ["staff_id"]

    unresolved_staff = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": 1},
    )
    assert unresolved_staff.status_code == 422
    assert unresolved_staff.json()["detail"]["missing_fields"] == ["staff_id"]


def test_real_candidates_persist_and_only_schedule_changes_make_them_stale(
    environment,
) -> None:
    client, _ = environment
    owner_id = f"owner_{uuid4().hex}"
    session_id = f"session_{uuid4().hex}"
    trusted_headers = headers(owner_id, session_id)

    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-full-groom",
            "preferred_date": OPEN_FRIDAY.isoformat(),
            "preferred_time_of_day": "morning",
            "staff_preference_text": "Lisa",
            "staff_id": "demo-staff-lisa",
        },
    ).json()["draft"]
    assert draft["version"] == 1
    assert draft["availability_revision"] == 1

    searched = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": 1},
    )
    assert searched.status_code == 200
    search = searched.json()["search"]
    assert search["status"] == "available"
    assert search["is_current"] is True
    assert search["availability_checked"] is True
    assert search["booking_created"] is False
    assert search["source"] == "postgresql_recurring_staff_schedule"
    assert 1 <= len(search["candidates"]) <= 8
    first = search["candidates"][0]
    assert first["staff_id"] == "demo-staff-lisa"
    assert datetime.fromisoformat(first["starts_at"]).date() == OPEN_FRIDAY
    assert datetime.fromisoformat(first["starts_at"]).time().isoformat() == "09:00:00"
    assert datetime.fromisoformat(first["ends_at"]).time().isoformat() == "11:00:00"
    assert datetime.fromisoformat(first["busy_starts_at"]).time().isoformat() == (
        "08:45:00"
    )
    assert datetime.fromisoformat(first["busy_ends_at"]).time().isoformat() == (
        "11:15:00"
    )

    after_phone = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 1, "contact_phone": "+1-415-555-0188"},
    ).json()["draft"]
    assert after_phone["version"] == 2
    assert after_phone["availability_revision"] == 1

    still_current = client.get(
        f"/api/internal/voice/availability-searches/{search['id']}",
        headers=trusted_headers,
    ).json()["search"]
    assert still_current["status"] == "available"
    assert still_current["is_current"] is True
    assert still_current["candidates"][0]["id"] == first["id"]

    changed_date = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 2, "preferred_date": NEXT_MONDAY.isoformat()},
    ).json()["draft"]
    assert changed_date["version"] == 3
    assert changed_date["availability_revision"] == 2

    stale = client.get(
        f"/api/internal/voice/availability-searches/{search['id']}",
        headers=trusted_headers,
    ).json()["search"]
    assert stale["status"] == "stale"
    assert stale["is_current"] is False
    assert stale["stale_reason"] == "draft_availability_changed"
    assert stale["candidates"] == []

    old_revision = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": 1},
    )
    assert old_revision.status_code == 409
    assert old_revision.json()["detail"] == {
        "code": "availability_revision_conflict",
        "current_revision": 2,
    }

    next_open_day = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": 2},
    ).json()["search"]
    assert datetime.fromisoformat(next_open_day["candidates"][0]["starts_at"]).date() == (
        NEXT_MONDAY
    )

    other_session = client.get(
        f"/api/internal/voice/availability-searches/{search['id']}",
        headers=headers(owner_id, f"session_{uuid4().hex}"),
    )
    assert other_session.status_code == 404


def test_closed_day_returns_real_alternatives_without_fake_success(environment) -> None:
    client, _ = environment
    trusted_headers = headers()
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-full-groom",
            "preferred_date": CLOSED_SUNDAY.isoformat(),
            "preferred_time_of_day": "flexible",
            "staff_id": "demo-staff-lisa",
        },
    ).json()["draft"]

    search = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": draft["availability_revision"]},
    ).json()["search"]

    assert search["status"] == "no_availability"
    assert search["candidates"] == []
    assert search["booking_created"] is False
    assert search["alternative_dates"][0]["date"] == NEXT_MONDAY.isoformat()
    assert datetime.fromisoformat(
        search["alternative_dates"][0]["first_start_at"]
    ).time().isoformat() == (
        "09:00:00"
    )


def test_service_time_of_day_and_staff_changes_each_invalidate_old_results(
    environment,
) -> None:
    client, _ = environment
    trusted_headers = headers()
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-bath",
            "preferred_date": OPEN_FRIDAY.isoformat(),
        },
    ).json()["draft"]

    def search_current(revision: int) -> dict:
        return client.post(
            "/api/internal/voice/availability-searches",
            headers=trusted_headers,
            json={"expected_availability_revision": revision},
        ).json()["search"]

    def assert_stale(search_id: str) -> None:
        result = client.get(
            f"/api/internal/voice/availability-searches/{search_id}",
            headers=trusted_headers,
        ).json()["search"]
        assert result["status"] == "stale"
        assert result["candidates"] == []

    first = search_current(draft["availability_revision"])
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 1, "preferred_time_of_day": "afternoon"},
    ).json()["draft"]
    assert draft["availability_revision"] == 2
    assert_stale(first["id"])

    second = search_current(draft["availability_revision"])
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 2, "staff_id": "demo-staff-lisa"},
    ).json()["draft"]
    assert draft["availability_revision"] == 3
    assert_stale(second["id"])

    third = search_current(draft["availability_revision"])
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={"expected_version": 3, "service_id": "demo-full-groom"},
    ).json()["draft"]
    assert draft["availability_revision"] == 4
    assert_stale(third["id"])


def test_unknown_or_unqualified_staff_is_rejected_without_state_change(
    environment,
) -> None:
    client, _ = environment

    unqualified_headers = headers()
    unqualified = client.patch(
        "/api/internal/voice/booking-draft",
        headers=unqualified_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-full-groom",
            "preferred_date": OPEN_FRIDAY.isoformat(),
            "staff_id": "demo-staff-marco",
        },
    )
    assert unqualified.status_code == 422
    assert unqualified.json()["detail"] == {
        "code": "staff_not_qualified",
        "staff_id": "demo-staff-marco",
    }
    current = client.get(
        "/api/internal/voice/booking-draft",
        headers=unqualified_headers,
    ).json()["draft"]
    assert current["version"] == 0

    unknown = client.patch(
        "/api/internal/voice/booking-draft",
        headers=headers(),
        json={"expected_version": 0, "staff_id": "model-invented-staff"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_staff"


def test_past_date_is_rejected_in_the_merchant_time_zone(environment) -> None:
    client, _ = environment
    trusted_headers = headers()
    yesterday = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=1)
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-bath",
            "preferred_date": yesterday.isoformat(),
        },
    ).json()["draft"]

    response = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={
            "expected_availability_revision": draft["availability_revision"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "past_date",
        "preferred_date": yesterday.isoformat(),
    }


def test_real_schedule_change_invalidates_results_but_idempotent_seed_does_not(
    environment,
) -> None:
    client, store = environment
    trusted_headers = headers()
    draft = client.patch(
        "/api/internal/voice/booking-draft",
        headers=trusted_headers,
        json={
            "expected_version": 0,
            "service_id": "demo-bath",
            "preferred_date": OPEN_FRIDAY.isoformat(),
        },
    ).json()["draft"]
    search = client.post(
        "/api/internal/voice/availability-searches",
        headers=trusted_headers,
        json={"expected_availability_revision": draft["availability_revision"]},
    ).json()["search"]

    base_config = load_demo_merchant()
    store.upsert_merchant(base_config)
    unchanged = client.get(
        f"/api/internal/voice/availability-searches/{search['id']}",
        headers=trusted_headers,
    ).json()["search"]
    assert unchanged["is_current"] is True
    assert unchanged["schedule_revision"] == search["schedule_revision"]

    changed_config = base_config.model_copy(deep=True)
    changed_config.scheduling.service_buffers[0].buffer_before_minutes += 1
    try:
        store.upsert_merchant(changed_config)
        stale = client.get(
            f"/api/internal/voice/availability-searches/{search['id']}",
            headers=trusted_headers,
        ).json()["search"]
        assert stale["status"] == "stale"
        assert stale["stale_reason"] == "merchant_schedule_changed"
        assert stale["candidates"] == []
    finally:
        store.upsert_merchant(base_config)


def test_late_search_cannot_persist_after_date_changes(environment) -> None:
    _, store = environment
    trusted_context = context()
    initial = store.get_or_create_draft(trusted_context)
    prepared, _ = store.update_draft(
        trusted_context,
        initial.version,
        {
            "service_id": "demo-bath",
            "preferred_date": OPEN_FRIDAY,
        },
    )

    calculation_started = Event()
    allow_completion = Event()

    def slow_calculator(**kwargs):
        calculation_started.set()
        assert allow_completion.wait(timeout=5)
        return candidate_windows_for_date(**kwargs)

    slow_store = PostgresStore(store.engine, candidate_calculator=slow_calculator)
    outcome: dict[str, object] = {}

    def run_search() -> None:
        try:
            outcome["result"] = slow_store.search_availability(
                trusted_context,
                prepared.availability_revision,
            )
        except Exception as error:  # Captured for the assertion in the parent thread.
            outcome["error"] = error

    worker = Thread(target=run_search)
    worker.start()
    assert calculation_started.wait(timeout=5)
    store.update_draft(
        trusted_context,
        prepared.version,
        {"preferred_date": NEXT_MONDAY},
    )
    allow_completion.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert "result" not in outcome
    assert isinstance(outcome.get("error"), AvailabilityRevisionConflictError)
    assert outcome["error"].current_revision == prepared.availability_revision + 1
