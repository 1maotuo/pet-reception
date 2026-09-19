from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .availability import (
    CandidateWindow,
    OccupiedInterval,
    StaffWorkingWindow,
    candidate_windows_for_date,
    find_alternative_dates,
)
from .booking_service import (
    BookingAlreadySubmittedError,
    BookingConfirmationConflictError,
    BookingCredentialError,
    BookingNeedsInputError,
    BookingPaymentConflictError,
    BookingPaymentExpiredError,
    BookingPreparationExpiredError,
    BookingPreparationNotFoundError,
    BookingPreparationNotPresentedError,
    BookingService,
    CandidateUnavailableError,
    active_booking_condition,
    expire_unpaid_booking,
)
from .models import (
    availability_candidates,
    availability_searches,
    booking_drafts,
    booking_preparations,
    bookings,
    merchant_services,
    merchants,
    staff_members,
    staff_service_qualifications,
    staff_working_hours,
)
from .schemas import (
    AlternativeAvailabilityDate,
    AvailabilityCandidate,
    AvailabilitySearchResult,
    BookingOperationResult,
    BookingRecord,
    BookingDraft,
    DRAFT_MUTABLE_FIELDS,
    DemoMerchant,
    DraftFieldName,
    MerchantStaff,
    MerchantService,
    MerchantBookingList,
    VoiceRequestContext,
)


class MerchantNotFoundError(LookupError):
    pass


class UnknownServiceError(ValueError):
    pass


class UnknownStaffError(ValueError):
    pass


class StaffNotQualifiedError(ValueError):
    pass


class AvailabilitySearchNotFoundError(LookupError):
    pass


class AvailabilityPastDateError(ValueError):
    pass


@dataclass(slots=True)
class AvailabilityScheduleChangedError(Exception):
    current_revision: int


@dataclass(slots=True)
class DraftVersionConflictError(Exception):
    current_version: int


@dataclass(slots=True)
class AvailabilityRevisionConflictError(Exception):
    current_revision: int


@dataclass(slots=True)
class AvailabilityNeedsInputError(Exception):
    missing_fields: tuple[str, ...]


AVAILABILITY_FIELDS = frozenset(
    {
        "service_id",
        "preferred_date",
        "preferred_time_of_day",
        "staff_preference_text",
        "staff_id",
    }
)


class DataStore(Protocol):
    def get_merchant(self, merchant_id: str) -> DemoMerchant | None: ...

    def get_or_create_draft(self, context: VoiceRequestContext) -> BookingDraft: ...

    def update_draft(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        changes: dict[DraftFieldName, object],
    ) -> tuple[BookingDraft, list[DraftFieldName]]: ...

    def search_availability(
        self,
        context: VoiceRequestContext,
        expected_availability_revision: int,
    ) -> AvailabilitySearchResult: ...

    def get_availability_search(
        self,
        context: VoiceRequestContext,
        search_id: UUID,
    ) -> AvailabilitySearchResult: ...

    def prepare_booking(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        candidate_id: UUID,
    ) -> BookingOperationResult: ...

    def mark_booking_preparation_presented(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        expected_draft_version: int,
        presentation_id: str,
    ) -> BookingOperationResult: ...

    def confirm_booking(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        expected_draft_version: int,
        confirmation_event_id: str,
    ) -> BookingOperationResult: ...

    def get_booking_status(
        self, context: VoiceRequestContext, operation_id: UUID
    ) -> BookingOperationResult: ...

    def get_customer_booking(
        self, customer_reference: str, access_token: str
    ) -> BookingRecord: ...

    def simulate_payment(
        self,
        customer_reference: str,
        access_token: str,
        payment_event_id: str,
    ) -> BookingRecord: ...

    def list_merchant_bookings(self, merchant_id: str) -> MerchantBookingList: ...

    def get_merchant_booking(
        self, merchant_id: str, booking_id: UUID
    ) -> BookingRecord: ...


class PostgresStore:
    def __init__(
        self,
        engine: Engine,
        candidate_calculator: Callable[..., list[CandidateWindow]] = (
            candidate_windows_for_date
        ),
        now_provider: Callable[[ZoneInfo], datetime] = datetime.now,
        booking_hold_minutes: int = 5,
        payment_hold_minutes: int = 10,
        booking_credential_secret: str = "",
        web_origin: str = "http://127.0.0.1:5173",
    ) -> None:
        self.engine = engine
        self.candidate_calculator = candidate_calculator
        self.now_provider = now_provider
        self.booking_service = BookingService(
            engine,
            hold_minutes=booking_hold_minutes,
            payment_hold_minutes=payment_hold_minutes,
            credential_secret=booking_credential_secret,
            web_origin=web_origin,
            now_provider=lambda: self.now_provider(ZoneInfo("UTC")),
        )

    @staticmethod
    def _schedule_fingerprint(merchant: DemoMerchant) -> str | None:
        scheduling = getattr(merchant, "scheduling", None)
        if scheduling is None:
            return None
        payload = {
            "time_zone": merchant.time_zone,
            "services": [
                {
                    "id": service.id,
                    "duration_minutes": service.duration_minutes,
                }
                for service in merchant.services
            ],
            "scheduling": scheduling.model_dump(mode="json"),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get_merchant(self, merchant_id: str) -> DemoMerchant | None:
        with self.engine.connect() as connection:
            merchant_row = connection.execute(
                select(merchants).where(merchants.c.id == merchant_id)
            ).mappings().one_or_none()
            if merchant_row is None:
                return None
            service_rows = connection.execute(
                select(merchant_services)
                .where(
                    merchant_services.c.merchant_id == merchant_id,
                    merchant_services.c.active.is_(True),
                )
                .order_by(merchant_services.c.display_order, merchant_services.c.id)
            ).mappings()
            services = [
                MerchantService(
                    id=row["id"],
                    name_zh=row["name_zh"],
                    duration_minutes=row["duration_minutes"],
                    price_note=row["price_note"],
                )
                for row in service_rows
            ]
            staff_rows = list(
                connection.execute(
                    select(staff_members)
                    .where(
                        staff_members.c.merchant_id == merchant_id,
                        staff_members.c.active.is_(True),
                    )
                    .order_by(staff_members.c.display_name, staff_members.c.id)
                ).mappings()
            )
            qualification_rows = list(
                connection.execute(
                    select(staff_service_qualifications).where(
                        staff_service_qualifications.c.merchant_id == merchant_id
                    )
                ).mappings()
            )
            service_ids_by_staff: dict[str, list[str]] = {}
            for qualification in qualification_rows:
                service_ids_by_staff.setdefault(qualification["staff_id"], []).append(
                    qualification["service_id"]
                )
            staff = [
                MerchantStaff(
                    id=row["id"],
                    display_name=row["display_name"],
                    service_ids=sorted(service_ids_by_staff.get(row["id"], [])),
                    source=row["source"],
                )
                for row in staff_rows
            ]
        return DemoMerchant(
            id=merchant_row["id"],
            display_name=merchant_row["display_name"],
            merchant_type=merchant_row["merchant_type"],
            time_zone=merchant_row["time_zone"],
            phone=merchant_row["phone"],
            address=merchant_row["address"],
            is_demo=merchant_row["is_demo"],
            source=merchant_row["source"],
            services=services,
            staff=staff,
        )

    def upsert_merchant(self, merchant: DemoMerchant) -> None:
        scheduling = getattr(merchant, "scheduling", None)
        schedule_fingerprint = self._schedule_fingerprint(merchant)
        merchant_values = {
            "id": merchant.id,
            "display_name": merchant.display_name,
            "merchant_type": merchant.merchant_type,
            "time_zone": merchant.time_zone,
            "phone": merchant.phone,
            "address": merchant.address,
            "is_demo": merchant.is_demo,
            "source": merchant.source,
            "slot_interval_minutes": (
                scheduling.slot_interval_minutes if scheduling else 30
            ),
            "candidate_limit": scheduling.candidate_limit if scheduling else 8,
            "alternative_days": scheduling.alternative_days if scheduling else 7,
            "schedule_revision": 1 if scheduling else 0,
            "schedule_fingerprint": schedule_fingerprint,
        }
        service_ids = {service.id for service in merchant.services}
        buffer_by_service = {
            item.service_id: item for item in scheduling.service_buffers
        } if scheduling else {}
        unknown_buffer_services = set(buffer_by_service).difference(service_ids)
        if unknown_buffer_services:
            raise UnknownServiceError(sorted(unknown_buffer_services)[0])
        current_staff_ids = {
            staff.id for staff in scheduling.staff
        } if scheduling else set()
        if scheduling:
            for staff in scheduling.staff:
                unknown_staff_services = set(staff.service_ids).difference(service_ids)
                if unknown_staff_services:
                    raise UnknownServiceError(sorted(unknown_staff_services)[0])
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(merchants)
                .values(**merchant_values)
                .on_conflict_do_update(
                    index_elements=[merchants.c.id],
                    set_={
                        **{
                            key: value
                            for key, value in merchant_values.items()
                            if key
                            not in {"id", "schedule_revision", "schedule_fingerprint"}
                        },
                        "schedule_revision": case(
                            (
                                merchants.c.schedule_fingerprint.is_distinct_from(
                                    schedule_fingerprint
                                ),
                                merchants.c.schedule_revision + 1,
                            ),
                            else_=merchants.c.schedule_revision,
                        ),
                        "schedule_fingerprint": schedule_fingerprint,
                        "updated_at": func.now(),
                    },
                )
            )
            connection.execute(
                merchant_services.update()
                .where(merchant_services.c.merchant_id == merchant.id)
                .values(active=False, updated_at=func.now())
            )
            for display_order, service in enumerate(merchant.services):
                service_values = {
                    "merchant_id": merchant.id,
                    "id": service.id,
                    "name_zh": service.name_zh,
                    "duration_minutes": service.duration_minutes,
                    "price_note": service.price_note,
                    "active": True,
                    "display_order": display_order,
                    "buffer_before_minutes": (
                        buffer_by_service[service.id].buffer_before_minutes
                        if service.id in buffer_by_service
                        else 0
                    ),
                    "buffer_after_minutes": (
                        buffer_by_service[service.id].buffer_after_minutes
                        if service.id in buffer_by_service
                        else 0
                    ),
                }
                connection.execute(
                    pg_insert(merchant_services)
                    .values(**service_values)
                    .on_conflict_do_update(
                        index_elements=[
                            merchant_services.c.merchant_id,
                            merchant_services.c.id,
                        ],
                        set_={
                            **{
                                key: value
                                for key, value in service_values.items()
                                if key not in {"merchant_id", "id"}
                            },
                            "updated_at": func.now(),
                        },
                    )
                )

            connection.execute(
                staff_members.update()
                .where(staff_members.c.merchant_id == merchant.id)
                .values(active=False, updated_at=func.now())
            )
            removed_staff_condition = (
                staff_service_qualifications.c.merchant_id == merchant.id
            )
            removed_hours_condition = staff_working_hours.c.merchant_id == merchant.id
            if current_staff_ids:
                removed_staff_condition = removed_staff_condition & (
                    staff_service_qualifications.c.staff_id.not_in(current_staff_ids)
                )
                removed_hours_condition = removed_hours_condition & (
                    staff_working_hours.c.staff_id.not_in(current_staff_ids)
                )
            connection.execute(
                delete(staff_service_qualifications).where(removed_staff_condition)
            )
            connection.execute(delete(staff_working_hours).where(removed_hours_condition))

            for staff in scheduling.staff if scheduling else []:
                connection.execute(
                    pg_insert(staff_members)
                    .values(
                        merchant_id=merchant.id,
                        id=staff.id,
                        display_name=staff.display_name,
                        active=True,
                        source=staff.source,
                    )
                    .on_conflict_do_update(
                        index_elements=[staff_members.c.merchant_id, staff_members.c.id],
                        set_={
                            "display_name": staff.display_name,
                            "active": True,
                            "source": staff.source,
                            "updated_at": func.now(),
                        },
                    )
                )
                connection.execute(
                    delete(staff_service_qualifications).where(
                        staff_service_qualifications.c.merchant_id == merchant.id,
                        staff_service_qualifications.c.staff_id == staff.id,
                    )
                )
                connection.execute(
                    delete(staff_working_hours).where(
                        staff_working_hours.c.merchant_id == merchant.id,
                        staff_working_hours.c.staff_id == staff.id,
                    )
                )
                connection.execute(
                    staff_service_qualifications.insert(),
                    [
                        {
                            "merchant_id": merchant.id,
                            "staff_id": staff.id,
                            "service_id": service_id,
                        }
                        for service_id in staff.service_ids
                    ],
                )
                connection.execute(
                    staff_working_hours.insert(),
                    [
                        {
                            "id": uuid4(),
                            "merchant_id": merchant.id,
                            "staff_id": staff.id,
                            "weekday": window.weekday,
                            "start_time": window.start_time,
                            "end_time": window.end_time,
                        }
                        for window in staff.weekly_hours
                    ],
                )

            invalid_service = booking_drafts.c.service_id.is_not(None)
            if service_ids:
                invalid_service = invalid_service & booking_drafts.c.service_id.not_in(
                    service_ids
                )
            invalid_staff = booking_drafts.c.staff_id.is_not(None)
            if current_staff_ids:
                invalid_staff = invalid_staff & booking_drafts.c.staff_id.not_in(
                    current_staff_ids
                )
            valid_qualification = (
                select(staff_service_qualifications.c.staff_id)
                .where(
                    staff_service_qualifications.c.merchant_id
                    == booking_drafts.c.merchant_id,
                    staff_service_qualifications.c.staff_id
                    == booking_drafts.c.staff_id,
                    staff_service_qualifications.c.service_id
                    == booking_drafts.c.service_id,
                )
                .exists()
            )
            invalid_qualification = (
                booking_drafts.c.staff_id.is_not(None)
                & booking_drafts.c.service_id.is_not(None)
                & ~valid_qualification
            )
            invalid_staff_assignment = or_(
                invalid_service,
                invalid_staff,
                invalid_qualification,
            )
            connection.execute(
                booking_drafts.update()
                .where(
                    booking_drafts.c.merchant_id == merchant.id,
                    booking_drafts.c.booking_id.is_(None),
                    or_(invalid_service, invalid_staff, invalid_qualification),
                )
                .values(
                    service_id=case(
                        (invalid_service, None),
                        else_=booking_drafts.c.service_id,
                    ),
                    staff_id=case(
                        (invalid_staff_assignment, None),
                        else_=booking_drafts.c.staff_id,
                    ),
                    version=booking_drafts.c.version + 1,
                    availability_revision=booking_drafts.c.availability_revision + 1,
                    updated_at=func.now(),
                )
            )

    def _draft_identity(self, context: VoiceRequestContext) -> dict[str, str]:
        return {
            "merchant_id": context.merchant_id,
            "owner_id": context.owner_id,
            "voice_session_id": context.voice_session_id,
        }

    @staticmethod
    def _lock_merchant(connection, merchant_id: str) -> None:
        merchant_id_result = connection.execute(
            select(merchants.c.id)
            .where(merchants.c.id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if merchant_id_result is None:
            raise MerchantNotFoundError(merchant_id)

    def _ensure_draft(self, connection, context: VoiceRequestContext) -> None:
        merchant_exists = connection.execute(
            select(merchants.c.id).where(merchants.c.id == context.merchant_id)
        ).scalar_one_or_none()
        if merchant_exists is None:
            raise MerchantNotFoundError(context.merchant_id)
        connection.execute(
            pg_insert(booking_drafts)
            .values(id=uuid4(), **self._draft_identity(context))
            .on_conflict_do_nothing(
                index_elements=[
                    booking_drafts.c.merchant_id,
                    booking_drafts.c.owner_id,
                    booking_drafts.c.voice_session_id,
                ]
            )
        )

    def _select_draft(self, connection, context: VoiceRequestContext, *, for_update=False):
        statement = select(booking_drafts).where(
            booking_drafts.c.merchant_id == context.merchant_id,
            booking_drafts.c.owner_id == context.owner_id,
            booking_drafts.c.voice_session_id == context.voice_session_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one()

    def get_or_create_draft(self, context: VoiceRequestContext) -> BookingDraft:
        with self.engine.begin() as connection:
            self._lock_merchant(connection, context.merchant_id)
            self._ensure_draft(connection, context)
            row = self._select_draft(connection, context, for_update=True)
            if row["booking_id"] is not None:
                booking = connection.execute(
                    select(bookings)
                    .where(bookings.c.id == row["booking_id"])
                    .with_for_update()
                ).mappings().one_or_none()
                if booking is not None:
                    expire_unpaid_booking(
                        connection,
                        booking,
                        self.now_provider(ZoneInfo("UTC")),
                    )
                    row = self._select_draft(connection, context, for_update=True)
            if row["pending_preparation_id"] is not None:
                pending = connection.execute(
                    select(booking_preparations).where(
                        booking_preparations.c.id == row["pending_preparation_id"]
                    )
                ).mappings().one_or_none()
                merchant_now = self.now_provider(ZoneInfo("UTC"))
                pending_is_expired = pending is not None and (
                    pending["status"] == "expired"
                    or (
                        pending["status"] in {"prepared", "presented"}
                        and pending["expires_at"] <= merchant_now
                    )
                )
                if pending_is_expired:
                    if pending["status"] != "expired":
                        connection.execute(
                            booking_preparations.update()
                            .where(booking_preparations.c.id == pending["id"])
                            .values(status="expired", updated_at=func.now())
                        )
                    connection.execute(
                        booking_drafts.update()
                        .where(booking_drafts.c.id == row["id"])
                        .values(
                            selected_candidate_id=None,
                            pending_preparation_id=None,
                            version=row["version"] + 1,
                            updated_at=func.now(),
                        )
                    )
                    row = self._select_draft(connection, context)
        return BookingDraft.model_validate(dict(row))

    def update_draft(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        changes: dict[DraftFieldName, object],
    ) -> tuple[BookingDraft, list[DraftFieldName]]:
        with self.engine.begin() as connection:
            self._lock_merchant(connection, context.merchant_id)
            self._ensure_draft(connection, context)
            current = self._select_draft(connection, context, for_update=True)
            if current["booking_id"] is not None:
                booking = connection.execute(
                    select(bookings)
                    .where(bookings.c.id == current["booking_id"])
                    .with_for_update()
                ).mappings().one_or_none()
                if booking is not None:
                    expire_unpaid_booking(
                        connection,
                        booking,
                        self.now_provider(ZoneInfo("UTC")),
                    )
                    current = self._select_draft(connection, context, for_update=True)
            if current["booking_id"] is not None:
                raise BookingAlreadySubmittedError(str(current["booking_id"]))
            if current["version"] != expected_version:
                raise DraftVersionConflictError(current_version=current["version"])

            normalized_changes = dict(changes)
            preference_changed = (
                "staff_preference_text" in normalized_changes
                and normalized_changes["staff_preference_text"]
                != current["staff_preference_text"]
            )
            if preference_changed and "staff_id" not in normalized_changes:
                normalized_changes["staff_id"] = None

            service_id = normalized_changes.get("service_id")
            if service_id is not None:
                service_exists = connection.execute(
                    select(merchant_services.c.id).where(
                        merchant_services.c.merchant_id == context.merchant_id,
                        merchant_services.c.id == service_id,
                        merchant_services.c.active.is_(True),
                    )
                ).scalar_one_or_none()
                if service_exists is None:
                    raise UnknownServiceError(str(service_id))

            effective_service_id = normalized_changes.get(
                "service_id", current["service_id"]
            )
            effective_staff_id = normalized_changes.get("staff_id", current["staff_id"])
            if effective_staff_id is not None and {"staff_id", "service_id"}.intersection(
                normalized_changes
            ):
                staff_exists = connection.execute(
                    select(staff_members.c.id).where(
                        staff_members.c.merchant_id == context.merchant_id,
                        staff_members.c.id == effective_staff_id,
                        staff_members.c.active.is_(True),
                    )
                ).scalar_one_or_none()
                if staff_exists is None:
                    raise UnknownStaffError(str(effective_staff_id))
                if effective_service_id is not None:
                    qualified = connection.execute(
                        select(staff_service_qualifications.c.staff_id).where(
                            staff_service_qualifications.c.merchant_id
                            == context.merchant_id,
                            staff_service_qualifications.c.staff_id
                            == effective_staff_id,
                            staff_service_qualifications.c.service_id
                            == effective_service_id,
                        )
                    ).scalar_one_or_none()
                    if qualified is None:
                        raise StaffNotQualifiedError(str(effective_staff_id))

            changed_fields = [
                field
                for field in DRAFT_MUTABLE_FIELDS
                if field in normalized_changes
                and current[field] != normalized_changes[field]
            ]
            if not changed_fields:
                return BookingDraft.model_validate(dict(current)), []

            update_values = {
                field: normalized_changes[field] for field in changed_fields
            }
            if "contact_phone" in changed_fields:
                update_values["contact_phone_source"] = (
                    "voice_user_provided"
                    if update_values["contact_phone"] is not None
                    else None
                )
            update_values.update(
                version=current["version"] + 1,
                updated_at=func.now(),
            )
            if AVAILABILITY_FIELDS.intersection(changed_fields):
                update_values["availability_revision"] = (
                    current["availability_revision"] + 1
                )
            if current["pending_preparation_id"] is not None:
                connection.execute(
                    booking_preparations.update()
                    .where(
                        booking_preparations.c.id == current["pending_preparation_id"],
                        booking_preparations.c.status.in_(["prepared", "presented"]),
                    )
                    .values(status="cancelled", updated_at=func.now())
                )
                update_values["selected_candidate_id"] = None
                update_values["pending_preparation_id"] = None
            connection.execute(
                booking_drafts.update()
                .where(booking_drafts.c.id == current["id"])
                .values(**update_values)
            )
            updated = self._select_draft(connection, context)
        return BookingDraft.model_validate(dict(updated)), changed_fields

    @staticmethod
    def _occupied_intervals(connection, merchant_id: str, now_utc: datetime):
        booked_rows = connection.execute(
            select(
                bookings.c.staff_id,
                bookings.c.busy_starts_at,
                bookings.c.busy_ends_at,
            ).where(
                bookings.c.merchant_id == merchant_id,
                bookings.c.busy_ends_at > now_utc,
                active_booking_condition(now_utc),
            )
        ).mappings()
        held_rows = connection.execute(
            select(
                booking_preparations.c.staff_id,
                booking_preparations.c.busy_starts_at,
                booking_preparations.c.busy_ends_at,
            ).where(
                booking_preparations.c.merchant_id == merchant_id,
                booking_preparations.c.status.in_(["prepared", "presented"]),
                booking_preparations.c.expires_at > now_utc,
            )
        ).mappings()
        return [
            OccupiedInterval(
                staff_id=row["staff_id"],
                busy_starts_at=row["busy_starts_at"],
                busy_ends_at=row["busy_ends_at"],
            )
            for row in [*booked_rows, *held_rows]
        ]

    @staticmethod
    def _interval_is_available(
        staff_id: str,
        busy_starts_at: datetime,
        busy_ends_at: datetime,
        occupied: list[OccupiedInterval],
    ) -> bool:
        busy_start_utc = busy_starts_at.astimezone(timezone.utc)
        busy_end_utc = busy_ends_at.astimezone(timezone.utc)
        return not any(
            interval.staff_id == staff_id
            and busy_start_utc < interval.busy_ends_at.astimezone(timezone.utc)
            and interval.busy_starts_at.astimezone(timezone.utc) < busy_end_utc
            for interval in occupied
        )

    def _load_availability_inputs(
        self,
        context: VoiceRequestContext,
        expected_availability_revision: int,
    ) -> tuple[
        dict,
        dict,
        dict,
        list[StaffWorkingWindow],
        list[OccupiedInterval],
    ]:
        with self.engine.begin() as connection:
            self._ensure_draft(connection, context)
            draft = dict(self._select_draft(connection, context))
            if draft["availability_revision"] != expected_availability_revision:
                raise AvailabilityRevisionConflictError(
                    current_revision=draft["availability_revision"]
                )
            missing = tuple(
                field
                for field in ("service_id", "preferred_date")
                if draft[field] is None
            )
            if draft["staff_preference_text"] and not draft["staff_id"]:
                missing += ("staff_id",)
            if missing:
                raise AvailabilityNeedsInputError(missing_fields=missing)

            merchant = connection.execute(
                select(merchants).where(merchants.c.id == context.merchant_id)
            ).mappings().one_or_none()
            if merchant is None:
                raise MerchantNotFoundError(context.merchant_id)
            merchant_today = self.now_provider(ZoneInfo(merchant["time_zone"])).date()
            if draft["preferred_date"] < merchant_today:
                raise AvailabilityPastDateError(draft["preferred_date"].isoformat())
            service = connection.execute(
                select(merchant_services).where(
                    merchant_services.c.merchant_id == context.merchant_id,
                    merchant_services.c.id == draft["service_id"],
                    merchant_services.c.active.is_(True),
                )
            ).mappings().one_or_none()
            if service is None:
                raise UnknownServiceError(str(draft["service_id"]))

            statement = (
                select(
                    staff_members.c.id.label("staff_id"),
                    staff_members.c.display_name.label("staff_display_name"),
                    staff_working_hours.c.weekday,
                    staff_working_hours.c.start_time,
                    staff_working_hours.c.end_time,
                )
                .select_from(
                    staff_members.join(
                        staff_service_qualifications,
                        (staff_service_qualifications.c.merchant_id
                         == staff_members.c.merchant_id)
                        & (staff_service_qualifications.c.staff_id == staff_members.c.id),
                    ).join(
                        staff_working_hours,
                        (staff_working_hours.c.merchant_id == staff_members.c.merchant_id)
                        & (staff_working_hours.c.staff_id == staff_members.c.id),
                    )
                )
                .where(
                    staff_members.c.merchant_id == context.merchant_id,
                    staff_members.c.active.is_(True),
                    staff_service_qualifications.c.service_id == draft["service_id"],
                )
            )
            if draft["staff_id"] is not None:
                statement = statement.where(staff_members.c.id == draft["staff_id"])
            rows = connection.execute(statement).mappings()
            windows = [
                StaffWorkingWindow(
                    staff_id=row["staff_id"],
                    staff_display_name=row["staff_display_name"],
                    weekday=row["weekday"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                )
                for row in rows
            ]
            now_utc = self.now_provider(ZoneInfo("UTC"))
            occupied = self._occupied_intervals(
                connection,
                context.merchant_id,
                now_utc,
            )
        return draft, dict(merchant), dict(service), windows, occupied

    def search_availability(
        self,
        context: VoiceRequestContext,
        expected_availability_revision: int,
    ) -> AvailabilitySearchResult:
        draft, merchant, service, windows, occupied = self._load_availability_inputs(
            context,
            expected_availability_revision,
        )
        merchant_now = self.now_provider(ZoneInfo(merchant["time_zone"]))
        candidates = self.candidate_calculator(
            requested_date=draft["preferred_date"],
            merchant_time_zone=merchant["time_zone"],
            duration_minutes=service["duration_minutes"],
            buffer_before_minutes=service["buffer_before_minutes"],
            buffer_after_minutes=service["buffer_after_minutes"],
            slot_interval_minutes=merchant["slot_interval_minutes"],
            preferred_time_of_day=draft["preferred_time_of_day"],
            windows=windows,
            limit=merchant["candidate_limit"],
            not_before=merchant_now,
            occupied_intervals=occupied,
        )
        alternatives = []
        if not candidates:
            alternatives = find_alternative_dates(
                requested_date=draft["preferred_date"],
                alternative_days=merchant["alternative_days"],
                candidate_limit=merchant["candidate_limit"],
                merchant_time_zone=merchant["time_zone"],
                duration_minutes=service["duration_minutes"],
                buffer_before_minutes=service["buffer_before_minutes"],
                buffer_after_minutes=service["buffer_after_minutes"],
                slot_interval_minutes=merchant["slot_interval_minutes"],
                preferred_time_of_day=draft["preferred_time_of_day"],
                windows=windows,
                occupied_intervals=occupied,
            )

        search_id = uuid4()
        created_at = datetime.now(timezone.utc)
        candidate_values = []
        candidate_models = []
        for display_order, candidate in enumerate(candidates):
            candidate_id = uuid4()
            value = {
                "id": candidate_id,
                "search_id": search_id,
                "merchant_id": context.merchant_id,
                "staff_id": candidate.staff_id,
                "service_id": draft["service_id"],
                "staff_display_name": candidate.staff_display_name,
                "starts_at": candidate.starts_at,
                "ends_at": candidate.ends_at,
                "busy_starts_at": candidate.busy_starts_at,
                "busy_ends_at": candidate.busy_ends_at,
                "merchant_time_zone": merchant["time_zone"],
                "display_order": display_order,
            }
            candidate_values.append(value)
            candidate_models.append(AvailabilityCandidate.model_validate(value))

        with self.engine.begin() as connection:
            self._lock_merchant(connection, context.merchant_id)
            current = self._select_draft(connection, context, for_update=True)
            if current["availability_revision"] != expected_availability_revision:
                raise AvailabilityRevisionConflictError(
                    current_revision=current["availability_revision"]
                )
            current_schedule_revision = connection.execute(
                select(merchants.c.schedule_revision).where(
                    merchants.c.id == context.merchant_id
                )
            ).scalar_one()
            if current_schedule_revision != merchant["schedule_revision"]:
                raise AvailabilityScheduleChangedError(
                    current_revision=current_schedule_revision
                )
            current_occupied = self._occupied_intervals(
                connection,
                context.merchant_id,
                self.now_provider(ZoneInfo("UTC")),
            )
            available_ids = {
                value["id"]
                for value in candidate_values
                if self._interval_is_available(
                    value["staff_id"],
                    value["busy_starts_at"],
                    value["busy_ends_at"],
                    current_occupied,
                )
            }
            candidate_values = [
                value for value in candidate_values if value["id"] in available_ids
            ]
            candidate_models = [
                candidate for candidate in candidate_models if candidate.id in available_ids
            ]
            if candidate_values:
                alternatives = []
            else:
                alternatives = find_alternative_dates(
                    requested_date=draft["preferred_date"],
                    alternative_days=merchant["alternative_days"],
                    candidate_limit=merchant["candidate_limit"],
                    merchant_time_zone=merchant["time_zone"],
                    duration_minutes=service["duration_minutes"],
                    buffer_before_minutes=service["buffer_before_minutes"],
                    buffer_after_minutes=service["buffer_after_minutes"],
                    slot_interval_minutes=merchant["slot_interval_minutes"],
                    preferred_time_of_day=draft["preferred_time_of_day"],
                    windows=windows,
                    occupied_intervals=current_occupied,
                )
            alternative_values = [
                {
                    "date": alternative.date.isoformat(),
                    "first_start_at": alternative.first_start_at.isoformat(),
                    "available_staff_ids": list(alternative.available_staff_ids),
                }
                for alternative in alternatives
            ]
            status = "available" if candidate_values else "no_availability"
            connection.execute(
                availability_searches.insert().values(
                    id=search_id,
                    draft_id=current["id"],
                    merchant_id=context.merchant_id,
                    owner_id=context.owner_id,
                    voice_session_id=context.voice_session_id,
                    draft_version=current["version"],
                    availability_revision=current["availability_revision"],
                    schedule_revision=current_schedule_revision,
                    service_id=draft["service_id"],
                    preferred_date=draft["preferred_date"],
                    preferred_time_of_day=draft["preferred_time_of_day"],
                    staff_id=draft["staff_id"],
                    merchant_time_zone=merchant["time_zone"],
                    status=status,
                    alternative_dates=alternative_values,
                    source="postgresql_recurring_staff_schedule",
                    created_at=created_at,
                )
            )
            if candidate_values:
                connection.execute(availability_candidates.insert(), candidate_values)

        return AvailabilitySearchResult(
            id=search_id,
            status=status,
            is_current=True,
            draft_id=draft["id"],
            draft_version=current["version"],
            availability_revision=current["availability_revision"],
            schedule_revision=current_schedule_revision,
            service_id=draft["service_id"],
            preferred_date=draft["preferred_date"],
            preferred_time_of_day=draft["preferred_time_of_day"],
            staff_id=draft["staff_id"],
            merchant_time_zone=merchant["time_zone"],
            candidates=candidate_models,
            alternative_dates=[
                AlternativeAvailabilityDate.model_validate(value)
                for value in alternative_values
            ],
            created_at=created_at,
        )

    def get_availability_search(
        self,
        context: VoiceRequestContext,
        search_id: UUID,
    ) -> AvailabilitySearchResult:
        with self.engine.begin() as connection:
            self._lock_merchant(connection, context.merchant_id)
            self._ensure_draft(connection, context)
            draft = self._select_draft(connection, context)
            current_schedule_revision = connection.execute(
                select(merchants.c.schedule_revision).where(
                    merchants.c.id == context.merchant_id
                )
            ).scalar_one()
            search = connection.execute(
                select(availability_searches).where(
                    availability_searches.c.id == search_id,
                    availability_searches.c.merchant_id == context.merchant_id,
                    availability_searches.c.owner_id == context.owner_id,
                    availability_searches.c.voice_session_id
                    == context.voice_session_id,
                    availability_searches.c.draft_id == draft["id"],
                )
            ).mappings().one_or_none()
            if search is None:
                raise AvailabilitySearchNotFoundError(str(search_id))
            schedule_is_current = (
                search["schedule_revision"] == current_schedule_revision
            )
            draft_is_current = (
                search["availability_revision"] == draft["availability_revision"]
                and search["service_id"] == draft["service_id"]
                and search["preferred_date"] == draft["preferred_date"]
                and search["preferred_time_of_day"]
                == draft["preferred_time_of_day"]
                and search["staff_id"] == draft["staff_id"]
            )
            snapshot_is_current = schedule_is_current and draft_is_current
            candidate_rows = []
            occupied = []
            service = None
            if snapshot_is_current:
                candidate_rows = list(
                    connection.execute(
                        select(availability_candidates)
                        .where(availability_candidates.c.search_id == search_id)
                        .order_by(availability_candidates.c.display_order)
                    ).mappings()
                )
                occupied = self._occupied_intervals(
                    connection,
                    context.merchant_id,
                    self.now_provider(ZoneInfo("UTC")),
                )
                service = connection.execute(
                    select(
                        merchant_services.c.duration_minutes,
                        merchant_services.c.buffer_before_minutes,
                        merchant_services.c.buffer_after_minutes,
                    ).where(
                        merchant_services.c.merchant_id == context.merchant_id,
                        merchant_services.c.id == search["service_id"],
                    )
                ).mappings().one_or_none()

        zone = ZoneInfo(search["merchant_time_zone"])
        merchant_now = self.now_provider(zone)
        candidate_rows_before_occupancy = [
            row
            for row in candidate_rows
            if row["starts_at"].astimezone(zone) >= merchant_now
        ]
        candidate_rows = [
            row
            for row in candidate_rows_before_occupancy
            if self._interval_is_available(
                row["staff_id"],
                row["busy_starts_at"],
                row["busy_ends_at"],
                occupied,
            )
        ]
        candidates_removed_by_occupancy = (
            bool(candidate_rows_before_occupancy) and not candidate_rows
        )
        alternatives = (
            [
                AlternativeAvailabilityDate.model_validate(value)
                for value in search["alternative_dates"]
            ]
            if snapshot_is_current
            else []
        )
        alternatives = [
            alternative
            for alternative in alternatives
            if alternative.first_start_at.astimezone(zone) >= merchant_now
        ]
        if service is not None:
            refreshed_alternatives = []
            for alternative in alternatives:
                service_start_utc = alternative.first_start_at.astimezone(timezone.utc)
                busy_start = service_start_utc - timedelta(
                    minutes=service["buffer_before_minutes"]
                )
                busy_end = service_start_utc + timedelta(
                    minutes=(
                        service["duration_minutes"]
                        + service["buffer_after_minutes"]
                    )
                )
                available_staff_ids = [
                    staff_id
                    for staff_id in alternative.available_staff_ids
                    if self._interval_is_available(
                        staff_id,
                        busy_start,
                        busy_end,
                        occupied,
                    )
                ]
                if available_staff_ids:
                    refreshed_alternatives.append(
                        alternative.model_copy(
                            update={"available_staff_ids": available_staff_ids}
                        )
                    )
            alternatives = refreshed_alternatives
        time_is_current = search["preferred_date"] >= merchant_now.date()
        if search["status"] == "available":
            time_is_current = time_is_current and bool(candidate_rows)
        is_current = snapshot_is_current and time_is_current
        if not is_current:
            candidate_rows = []
            alternatives = []
        candidates = [
            AvailabilityCandidate(
                id=row["id"],
                staff_id=row["staff_id"],
                staff_display_name=row["staff_display_name"],
                service_id=row["service_id"],
                starts_at=row["starts_at"].astimezone(zone),
                ends_at=row["ends_at"].astimezone(zone),
                busy_starts_at=row["busy_starts_at"].astimezone(zone),
                busy_ends_at=row["busy_ends_at"].astimezone(zone),
                merchant_time_zone=row["merchant_time_zone"],
            )
            for row in candidate_rows
        ]
        return AvailabilitySearchResult(
            id=search["id"],
            status=search["status"] if is_current else "stale",
            is_current=is_current,
            stale_reason=(
                None
                if is_current
                else (
                    "merchant_schedule_changed"
                    if not schedule_is_current
                    else (
                        "draft_availability_changed"
                        if not draft_is_current
                        else (
                            "availability_occupied"
                            if candidates_removed_by_occupancy
                            else "availability_expired"
                        )
                    )
                )
            ),
            draft_id=search["draft_id"],
            draft_version=search["draft_version"],
            availability_revision=search["availability_revision"],
            schedule_revision=search["schedule_revision"],
            service_id=search["service_id"],
            preferred_date=search["preferred_date"],
            preferred_time_of_day=search["preferred_time_of_day"],
            staff_id=search["staff_id"],
            merchant_time_zone=search["merchant_time_zone"],
            candidates=candidates,
            alternative_dates=alternatives,
            created_at=search["created_at"],
        )

    def prepare_booking(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        candidate_id: UUID,
    ) -> BookingOperationResult:
        return self.booking_service.prepare(
            context,
            expected_version=expected_version,
            candidate_id=candidate_id,
        )

    def mark_booking_preparation_presented(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        expected_draft_version: int,
        presentation_id: str,
    ) -> BookingOperationResult:
        return self.booking_service.mark_presented(
            context,
            preparation_id,
            expected_draft_version=expected_draft_version,
            presentation_id=presentation_id,
        )

    def confirm_booking(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        expected_draft_version: int,
        confirmation_event_id: str,
    ) -> BookingOperationResult:
        return self.booking_service.confirm(
            context,
            preparation_id,
            expected_draft_version=expected_draft_version,
            confirmation_event_id=confirmation_event_id,
        )

    def get_booking_status(
        self,
        context: VoiceRequestContext,
        operation_id: UUID,
    ) -> BookingOperationResult:
        return self.booking_service.get_status(context, operation_id)

    def get_customer_booking(
        self,
        customer_reference: str,
        access_token: str,
    ) -> BookingRecord:
        return self.booking_service.get_customer_booking(
            customer_reference,
            access_token,
        )

    def simulate_payment(
        self,
        customer_reference: str,
        access_token: str,
        payment_event_id: str,
    ) -> BookingRecord:
        return self.booking_service.simulate_payment(
            customer_reference,
            access_token,
            payment_event_id,
        )

    def list_merchant_bookings(self, merchant_id: str) -> MerchantBookingList:
        return self.booking_service.list_merchant_bookings(merchant_id)

    def get_merchant_booking(
        self,
        merchant_id: str,
        booking_id: UUID,
    ) -> BookingRecord:
        return self.booking_service.get_merchant_booking(merchant_id, booking_id)
