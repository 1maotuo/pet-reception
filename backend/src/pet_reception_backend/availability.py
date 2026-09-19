from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = value - midnight
    interval = timedelta(minutes=interval_minutes)
    quotient, remainder = divmod(elapsed, interval)
    if remainder:
        quotient += 1
    return midnight + quotient * interval


def _unique_local_time(value: datetime, zone: ZoneInfo) -> datetime | None:
    """Resolve a naive wall time only when it maps to exactly one real instant."""
    matches: list[datetime] = []
    seen_instants: set[datetime] = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(timezone.utc)
        round_trip = instant.astimezone(zone)
        if round_trip.replace(tzinfo=None) != value or round_trip.fold != fold:
            continue
        if instant in seen_instants:
            continue
        seen_instants.add(instant)
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def _add_elapsed_minutes(value: datetime, minutes: int, zone: ZoneInfo) -> datetime:
    return (
        value.astimezone(timezone.utc) + timedelta(minutes=minutes)
    ).astimezone(zone)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


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
        working_start_wall = datetime.combine(requested_date, window.start_time)
        working_end_wall = datetime.combine(requested_date, window.end_time)
        working_start = _unique_local_time(working_start_wall, zone)
        working_end = _unique_local_time(working_end_wall, zone)
        if working_start is None or working_end is None:
            continue
        service_start_wall = _round_up(
            working_start_wall + timedelta(minutes=buffer_before_minutes),
            slot_interval_minutes,
        )
        while service_start_wall < working_end_wall:
            service_start = _unique_local_time(service_start_wall, zone)
            service_start_wall += timedelta(minutes=slot_interval_minutes)
            if service_start is None:
                continue
            service_end = _add_elapsed_minutes(service_start, duration_minutes, zone)
            busy_start = _add_elapsed_minutes(
                service_start,
                -buffer_before_minutes,
                zone,
            )
            busy_end = _add_elapsed_minutes(service_end, buffer_after_minutes, zone)
            interval_points = (busy_start, service_start, service_end, busy_end)
            if any(
                point.utcoffset() != service_start.utcoffset()
                for point in interval_points
            ):
                continue
            if _utc(busy_start) < _utc(working_start) or _utc(busy_end) > _utc(
                working_end
            ):
                continue
            key = (window.staff_id, service_start)
            if (
                key not in seen
                and (
                    not_before is None
                    or _utc(service_start) >= _utc(not_before)
                )
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
