from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class StaffWorkingWindow:
    staff_id: str
    staff_display_name: str
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True, slots=True)
class CandidateWindow:
    staff_id: str
    staff_display_name: str
    starts_at: datetime
    ends_at: datetime
    busy_starts_at: datetime
    busy_ends_at: datetime


@dataclass(frozen=True, slots=True)
class AlternativeDate:
    date: date
    first_start_at: datetime
    available_staff_ids: tuple[str, ...]


def _round_up(value: datetime, interval_minutes: int) -> datetime:
    minutes = value.hour * 60 + value.minute
    rounded = ((minutes + interval_minutes - 1) // interval_minutes) * interval_minutes
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=rounded)


def _matches_time_of_day(value: datetime, preference: str | None) -> bool:
    if preference in {None, "flexible"}:
        return True
    local_time = value.timetz().replace(tzinfo=None)
    if preference == "morning":
        return time(8, 0) <= local_time < time(12, 0)
    if preference == "afternoon":
        return time(12, 0) <= local_time < time(17, 0)
    return local_time >= time(17, 0)


def candidate_windows_for_date(
    *,
    requested_date: date,
    merchant_time_zone: str,
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
    slot_interval_minutes: int,
    preferred_time_of_day: str | None,
    windows: list[StaffWorkingWindow],
    limit: int,
    not_before: datetime | None = None,
) -> list[CandidateWindow]:
    zone = ZoneInfo(merchant_time_zone)
    results: list[CandidateWindow] = []
    seen: set[tuple[str, datetime]] = set()

    for window in windows:
        if window.weekday != requested_date.weekday():
            continue
        working_start = datetime.combine(requested_date, window.start_time, zone)
        working_end = datetime.combine(requested_date, window.end_time, zone)
        service_start = _round_up(
            working_start + timedelta(minutes=buffer_before_minutes),
            slot_interval_minutes,
        )
        while True:
            service_end = service_start + timedelta(minutes=duration_minutes)
            busy_start = service_start - timedelta(minutes=buffer_before_minutes)
            busy_end = service_end + timedelta(minutes=buffer_after_minutes)
            if busy_end > working_end:
                break
            key = (window.staff_id, service_start)
            if (
                key not in seen
                and (not_before is None or service_start >= not_before)
                and _matches_time_of_day(
                service_start,
                preferred_time_of_day,
                )
            ):
                seen.add(key)
                results.append(
                    CandidateWindow(
                        staff_id=window.staff_id,
                        staff_display_name=window.staff_display_name,
                        starts_at=service_start,
                        ends_at=service_end,
                        busy_starts_at=busy_start,
                        busy_ends_at=busy_end,
                    )
                )
            service_start += timedelta(minutes=slot_interval_minutes)

    results.sort(key=lambda item: (item.starts_at, item.staff_display_name, item.staff_id))
    return results[:limit]


def find_alternative_dates(
    *,
    requested_date: date,
    alternative_days: int,
    candidate_limit: int,
    merchant_time_zone: str,
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
    slot_interval_minutes: int,
    preferred_time_of_day: str | None,
    windows: list[StaffWorkingWindow],
) -> list[AlternativeDate]:
    alternatives: list[AlternativeDate] = []
    for day_offset in range(1, alternative_days + 1):
        alternative_date = requested_date + timedelta(days=day_offset)
        candidates = candidate_windows_for_date(
            requested_date=alternative_date,
            merchant_time_zone=merchant_time_zone,
            duration_minutes=duration_minutes,
            buffer_before_minutes=buffer_before_minutes,
            buffer_after_minutes=buffer_after_minutes,
            slot_interval_minutes=slot_interval_minutes,
            preferred_time_of_day=preferred_time_of_day,
            windows=windows,
            limit=candidate_limit,
        )
        if not candidates:
            continue
        alternatives.append(
            AlternativeDate(
                date=alternative_date,
                first_start_at=candidates[0].starts_at,
                available_staff_ids=tuple(
                    sorted({candidate.staff_id for candidate in candidates})
                ),
            )
        )
        if len(alternatives) == 3:
            break
    return alternatives
