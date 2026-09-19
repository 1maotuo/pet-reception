from datetime import date, time, timezone

from pet_reception_backend.availability import (
    StaffWorkingWindow,
    candidate_windows_for_date,
    find_alternative_dates,
)


WINDOWS = [
    StaffWorkingWindow(
        staff_id="lisa",
        staff_display_name="Lisa",
        weekday=0,
        start_time=time(8, 45),
        end_time=time(12, 15),
    ),
    StaffWorkingWindow(
        staff_id="lisa",
        staff_display_name="Lisa",
        weekday=0,
        start_time=time(12, 45),
        end_time=time(17, 15),
    ),
]


def test_candidates_include_service_duration_and_busy_buffers() -> None:
    candidates = candidate_windows_for_date(
        requested_date=date(2026, 11, 2),
        merchant_time_zone="America/Los_Angeles",
        duration_minutes=120,
        buffer_before_minutes=15,
        buffer_after_minutes=15,
        slot_interval_minutes=30,
        preferred_time_of_day="morning",
        windows=WINDOWS,
        limit=8,
    )

    first = candidates[0]
    assert first.starts_at.isoformat() == "2026-11-02T09:00:00-08:00"
    assert first.ends_at.isoformat() == "2026-11-02T11:00:00-08:00"
    assert first.busy_starts_at.isoformat() == "2026-11-02T08:45:00-08:00"
    assert first.busy_ends_at.isoformat() == "2026-11-02T11:15:00-08:00"
    assert all(candidate.starts_at.hour < 12 for candidate in candidates)


def test_time_of_day_filter_and_limit_are_applied() -> None:
    candidates = candidate_windows_for_date(
        requested_date=date(2026, 11, 2),
        merchant_time_zone="America/Los_Angeles",
        duration_minutes=60,
        buffer_before_minutes=10,
        buffer_after_minutes=10,
        slot_interval_minutes=30,
        preferred_time_of_day="afternoon",
        windows=WINDOWS,
        limit=2,
    )

    assert len(candidates) == 2
    assert all(12 <= candidate.starts_at.hour < 17 for candidate in candidates)


def test_closed_date_returns_real_next_open_date() -> None:
    alternatives = find_alternative_dates(
        requested_date=date(2026, 11, 1),
        alternative_days=7,
        candidate_limit=8,
        merchant_time_zone="America/Los_Angeles",
        duration_minutes=120,
        buffer_before_minutes=15,
        buffer_after_minutes=15,
        slot_interval_minutes=30,
        preferred_time_of_day="flexible",
        windows=WINDOWS,
    )

    assert alternatives[0].date == date(2026, 11, 2)
    assert alternatives[0].first_start_at.isoformat() == "2026-11-02T09:00:00-08:00"
    assert alternatives[0].available_staff_ids == ("lisa",)


def test_dst_transition_hours_never_create_invalid_or_wrong_duration_slots() -> None:
    transition_window = [
        StaffWorkingWindow(
            staff_id="night",
            staff_display_name="Night Staff",
            weekday=6,
            start_time=time(0, 0),
            end_time=time(5, 0),
        )
    ]

    for transition_date, unsupported_hours in (
        (date(2027, 3, 14), {1, 2}),
        (date(2026, 11, 1), {1}),
    ):
        candidates = candidate_windows_for_date(
            requested_date=transition_date,
            merchant_time_zone="America/Los_Angeles",
            duration_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            slot_interval_minutes=30,
            preferred_time_of_day="flexible",
            windows=transition_window,
            limit=24,
        )

        assert candidates
        assert all(
            candidate.starts_at.hour not in unsupported_hours
            for candidate in candidates
        )
        assert all(
            (
                candidate.ends_at.astimezone(timezone.utc)
                - candidate.starts_at.astimezone(timezone.utc)
            ).total_seconds()
            == 3600
            for candidate in candidates
        )
        assert all(
            candidate.starts_at.utcoffset() == candidate.ends_at.utcoffset()
            for candidate in candidates
        )


def test_slot_rounding_never_moves_a_second_precision_window_backwards() -> None:
    candidates = candidate_windows_for_date(
        requested_date=date(2026, 11, 2),
        merchant_time_zone="America/Los_Angeles",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        slot_interval_minutes=30,
        preferred_time_of_day="flexible",
        windows=[
            StaffWorkingWindow(
                staff_id="lisa",
                staff_display_name="Lisa",
                weekday=0,
                start_time=time(9, 0, 30),
                end_time=time(10, 31),
            )
        ],
        limit=8,
    )

    assert candidates[0].starts_at.time() == time(9, 30)
