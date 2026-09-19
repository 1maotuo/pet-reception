from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, and_, func, or_, select

from .models import (
    availability_candidates,
    availability_searches,
    booking_drafts,
    booking_preparations,
    bookings,
    merchant_services,
    merchants,
    staff_members,
)
from .schemas import (
    BookingOperationResult,
    BookingPreparation,
    BookingRecord,
    BookingSummary,
    CustomerBookingCredential,
    MerchantBookingList,
    VoiceRequestContext,
)


class BookingAlreadySubmittedError(ValueError):
    pass


@dataclass(slots=True)
class BookingNeedsInputError(ValueError):
    missing_fields: tuple[str, ...]


class CandidateUnavailableError(ValueError):
    pass


class BookingPreparationNotFoundError(LookupError):
    pass


class BookingPreparationExpiredError(ValueError):
    pass


class BookingPreparationNotPresentedError(ValueError):
    pass


class BookingConfirmationConflictError(ValueError):
    pass


class BookingCredentialError(PermissionError):
    pass


class BookingPaymentExpiredError(ValueError):
    pass


class BookingPaymentConflictError(ValueError):
    pass


class _ExpiryDetected(Exception):
    """Roll back the current attempt before persisting expiry separately."""


def _overlaps(table, *, staff_id: str, busy_start: datetime, busy_end: datetime):
    return (
        (table.c.staff_id == staff_id)
        & (table.c.busy_starts_at < busy_end)
        & (table.c.busy_ends_at > busy_start)
    )


def active_booking_condition(now: datetime):
    """SQL predicate for rows that still occupy a staff member's time."""
    return or_(
        bookings.c.status == "confirmed",
        and_(
            bookings.c.status == "awaiting_payment",
            bookings.c.payment_expires_at > now,
        ),
    )


def expire_unpaid_booking(connection, booking, now: datetime):
    """Expire one overdue payment hold and release its owning draft."""
    if (
        booking["status"] == "awaiting_payment"
        and booking["payment_expires_at"] <= now
    ):
        booking = connection.execute(
            bookings.update()
            .where(
                bookings.c.id == booking["id"],
                bookings.c.status == "awaiting_payment",
            )
            .values(status="payment_expired", payment_expired_at=now)
            .returning(bookings)
        ).mappings().one()
        connection.execute(
            booking_drafts.update()
            .where(
                booking_drafts.c.id == booking["draft_id"],
                booking_drafts.c.booking_id == booking["id"],
            )
            .values(
                booking_id=None,
                selected_candidate_id=None,
                pending_preparation_id=None,
                version=booking_drafts.c.version + 1,
                availability_revision=booking_drafts.c.availability_revision + 1,
                updated_at=now,
            )
        )
    return booking


class BookingService:
    def __init__(
        self,
        engine: Engine,
        *,
        hold_minutes: int = 5,
        payment_hold_minutes: int = 10,
        credential_secret: str = "",
        web_origin: str = "http://127.0.0.1:5173",
        now_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.engine = engine
        self.hold_minutes = hold_minutes
        self.payment_hold_minutes = payment_hold_minutes
        self.credential_secret = credential_secret
        self.web_origin = web_origin.rstrip("/")
        self.now_provider = now_provider

    @staticmethod
    def _identity(context: VoiceRequestContext):
        return (
            booking_preparations.c.merchant_id == context.merchant_id,
            booking_preparations.c.owner_id == context.owner_id,
            booking_preparations.c.voice_session_id == context.voice_session_id,
        )

    @staticmethod
    def _draft_statement(context: VoiceRequestContext):
        return select(booking_drafts).where(
            booking_drafts.c.merchant_id == context.merchant_id,
            booking_drafts.c.owner_id == context.owner_id,
            booking_drafts.c.voice_session_id == context.voice_session_id,
        )

    @staticmethod
    def _summary_digest(summary: dict) -> str:
        payload = json.dumps(
            summary,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def _credential_token(self, booking_id: UUID) -> str:
        if len(self.credential_secret) < 32:
            raise RuntimeError("BOOKING_CREDENTIAL_SECRET must contain at least 32 characters")
        digest = hmac.new(
            self.credential_secret.encode("utf-8"),
            f"pet-reception-booking:{booking_id}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _token_hash(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()

    def _credential(self, row) -> CustomerBookingCredential:
        token = self._credential_token(row["id"])
        return CustomerBookingCredential(
            booking_reference=row["customer_reference"],
            confirmation_code=row["confirmation_code"],
            access_token=token,
            credential_url=(
                f"{self.web_origin}/booking/{row['customer_reference']}#token={token}"
            ),
        )

    @staticmethod
    def _preparation_model(row) -> BookingPreparation:
        status = row["status"]
        return BookingPreparation(
            id=row["id"],
            operation_id=row["operation_id"],
            draft_id=row["draft_id"],
            draft_version=row["draft_version"],
            candidate_id=row["candidate_id"],
            status=status,
            summary=BookingSummary.model_validate(row["summary"]),
            summary_digest=row["summary_digest"],
            presentation_id=row["presentation_id"],
            expires_at=row["expires_at"],
            availability_held=status in {"prepared", "presented"},
            booking_created=status == "confirmed",
            created_at=row["created_at"],
        )

    @staticmethod
    def _booking_model(row) -> BookingRecord:
        summary = BookingSummary.model_validate(row["summary"])
        zone = ZoneInfo(row["merchant_time_zone"])
        return BookingRecord(
            id=row["id"],
            operation_id=row["operation_id"],
            preparation_id=row["preparation_id"],
            merchant_id=row["merchant_id"],
            merchant_display_name=summary.merchant_display_name,
            staff_id=row["staff_id"],
            staff_display_name=summary.staff_display_name,
            service_id=row["service_id"],
            service_name_zh=summary.service_name_zh,
            customer_name=row["customer_name"],
            contact_phone=row["contact_phone"],
            pet_name=row["pet_name"],
            pet_species=row["pet_species"],
            pet_breed=row["pet_breed"],
            pet_age_years=row["pet_age_years"],
            pet_care_notes=row["pet_care_notes"],
            need_description=row["need_description"],
            starts_at=row["starts_at"].astimezone(zone),
            ends_at=row["ends_at"].astimezone(zone),
            merchant_time_zone=row["merchant_time_zone"],
            customer_reference=row["customer_reference"],
            confirmation_code=row["confirmation_code"],
            payment_expires_at=row["payment_expires_at"],
            paid_at=row["paid_at"],
            payment_expired_at=row["payment_expired_at"],
            payment_method=row["payment_method"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _expire_row(connection, preparation, now: datetime, *, force: bool = False):
        status = preparation["status"]
        if status in {"prepared", "presented"} and (
            force or preparation["expires_at"] <= now
        ):
            connection.execute(
                booking_preparations.update()
                .where(booking_preparations.c.id == preparation["id"])
                .values(status="expired", updated_at=func.now())
            )
            status = "expired"
            preparation = dict(preparation)
            preparation["status"] = status
        if status == "expired":
            connection.execute(
                booking_drafts.update()
                .where(
                    booking_drafts.c.id == preparation["draft_id"],
                    booking_drafts.c.pending_preparation_id == preparation["id"],
                )
                .values(
                    selected_candidate_id=None,
                    pending_preparation_id=None,
                    version=booking_drafts.c.version + 1,
                    updated_at=func.now(),
                )
            )
        return preparation

    def _persist_expiration(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
    ) -> None:
        """Commit expiry cleanup after an action transaction has been rolled back."""
        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == context.merchant_id)
                .with_for_update()
            ).scalar_one_or_none()
            preparation = connection.execute(
                select(booking_preparations)
                .where(
                    booking_preparations.c.id == preparation_id,
                    *self._identity(context),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if preparation is None:
                return
            self._expire_row(
                connection,
                preparation,
                self.now_provider(),
                force=True,
            )

    @staticmethod
    def _expire_merchant_payment_holds(connection, merchant_id: str, now: datetime) -> None:
        overdue = list(
            connection.execute(
                select(bookings)
                .where(
                    bookings.c.merchant_id == merchant_id,
                    bookings.c.status == "awaiting_payment",
                    bookings.c.payment_expires_at <= now,
                )
                .with_for_update()
            ).mappings()
        )
        for booking in overdue:
            expire_unpaid_booking(connection, booking, now)

    def prepare(
        self,
        context: VoiceRequestContext,
        *,
        expected_version: int,
        candidate_id: UUID,
    ) -> BookingOperationResult:
        with self.engine.begin() as connection:
            merchant = connection.execute(
                select(merchants)
                .where(merchants.c.id == context.merchant_id)
                .with_for_update()
            ).mappings().one_or_none()
            if merchant is None:
                raise CandidateUnavailableError("merchant_not_found")
            draft = connection.execute(
                self._draft_statement(context).with_for_update()
            ).mappings().one_or_none()
            if draft is None:
                raise CandidateUnavailableError("draft_not_found")
            if draft["booking_id"] is not None:
                raise BookingAlreadySubmittedError(str(draft["booking_id"]))
            if draft["version"] != expected_version:
                raise BookingConfirmationConflictError("draft_version_changed")
            required = (
                "customer_name",
                "contact_phone",
                "pet_name",
                "pet_species",
                "service_id",
                "preferred_date",
            )
            missing = tuple(field for field in required if draft[field] is None)
            if missing:
                raise BookingNeedsInputError(missing_fields=missing)

            candidate = connection.execute(
                select(
                    availability_candidates,
                    availability_searches.c.draft_id.label("search_draft_id"),
                    availability_searches.c.owner_id.label("search_owner_id"),
                    availability_searches.c.voice_session_id.label("search_session_id"),
                    availability_searches.c.availability_revision.label(
                        "search_availability_revision"
                    ),
                    availability_searches.c.schedule_revision.label(
                        "search_schedule_revision"
                    ),
                    availability_searches.c.preferred_date.label("search_date"),
                    availability_searches.c.preferred_time_of_day.label(
                        "search_time_of_day"
                    ),
                    availability_searches.c.staff_id.label("search_staff_id"),
                )
                .select_from(
                    availability_candidates.join(
                        availability_searches,
                        availability_searches.c.id
                        == availability_candidates.c.search_id,
                    )
                )
                .where(
                    availability_candidates.c.id == candidate_id,
                    availability_candidates.c.merchant_id == context.merchant_id,
                    availability_searches.c.owner_id == context.owner_id,
                    availability_searches.c.voice_session_id
                    == context.voice_session_id,
                    availability_searches.c.draft_id == draft["id"],
                )
            ).mappings().one_or_none()
            if candidate is None:
                raise CandidateUnavailableError("candidate_not_found")
            if (
                candidate["search_availability_revision"]
                != draft["availability_revision"]
                or candidate["search_schedule_revision"]
                != merchant["schedule_revision"]
                or candidate["service_id"] != draft["service_id"]
                or candidate["search_date"] != draft["preferred_date"]
                or candidate["search_time_of_day"]
                != draft["preferred_time_of_day"]
                or candidate["search_staff_id"] != draft["staff_id"]
            ):
                raise CandidateUnavailableError("candidate_stale")
            service = connection.execute(
                select(merchant_services).where(
                    merchant_services.c.merchant_id == context.merchant_id,
                    merchant_services.c.id == candidate["service_id"],
                    merchant_services.c.active.is_(True),
                )
            ).mappings().one_or_none()
            staff = connection.execute(
                select(staff_members)
                .where(
                    staff_members.c.merchant_id == context.merchant_id,
                    staff_members.c.id == candidate["staff_id"],
                    staff_members.c.active.is_(True),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if service is None or staff is None:
                raise CandidateUnavailableError("catalog_changed")

            now = self.now_provider()
            self._expire_merchant_payment_holds(
                connection, context.merchant_id, now
            )
            if candidate["starts_at"] <= now:
                raise CandidateUnavailableError("candidate_expired")

            expired_preparations = list(
                connection.execute(
                    select(booking_preparations)
                    .where(
                        booking_preparations.c.merchant_id == context.merchant_id,
                        booking_preparations.c.status.in_(["prepared", "presented"]),
                        booking_preparations.c.expires_at <= now,
                    )
                    .with_for_update()
                ).mappings()
            )
            for expired_preparation in expired_preparations:
                self._expire_row(connection, expired_preparation, now)
            booked = connection.execute(
                select(bookings.c.id).where(
                    bookings.c.merchant_id == context.merchant_id,
                    active_booking_condition(now),
                    _overlaps(
                        bookings,
                        staff_id=candidate["staff_id"],
                        busy_start=candidate["busy_starts_at"],
                        busy_end=candidate["busy_ends_at"],
                    ),
                ).limit(1)
            ).scalar_one_or_none()
            held = connection.execute(
                select(booking_preparations.c.id).where(
                    booking_preparations.c.merchant_id == context.merchant_id,
                    booking_preparations.c.draft_id != draft["id"],
                    booking_preparations.c.status.in_(["prepared", "presented"]),
                    booking_preparations.c.expires_at > now,
                    _overlaps(
                        booking_preparations,
                        staff_id=candidate["staff_id"],
                        busy_start=candidate["busy_starts_at"],
                        busy_end=candidate["busy_ends_at"],
                    ),
                ).limit(1)
            ).scalar_one_or_none()
            if booked is not None or held is not None:
                raise CandidateUnavailableError("candidate_conflict")

            connection.execute(
                booking_preparations.update()
                .where(
                    booking_preparations.c.draft_id == draft["id"],
                    booking_preparations.c.status.in_(["prepared", "presented"]),
                )
                .values(status="cancelled", updated_at=func.now())
            )
            preparation_id = uuid4()
            operation_id = uuid4()
            prepared_version = draft["version"] + 1
            zone = ZoneInfo(merchant["time_zone"])
            summary_model = BookingSummary(
                merchant_id=context.merchant_id,
                merchant_display_name=merchant["display_name"],
                service_id=service["id"],
                service_name_zh=service["name_zh"],
                staff_id=staff["id"],
                staff_display_name=staff["display_name"],
                starts_at=candidate["starts_at"].astimezone(zone),
                ends_at=candidate["ends_at"].astimezone(zone),
                merchant_time_zone=merchant["time_zone"],
                customer_name=draft["customer_name"],
                contact_phone=draft["contact_phone"],
                pet_name=draft["pet_name"],
                pet_species=draft["pet_species"],
                pet_breed=draft["pet_breed"],
                pet_age_years=(
                    Decimal(draft["pet_age_years"])
                    if draft["pet_age_years"] is not None
                    else None
                ),
                pet_care_notes=draft["pet_care_notes"],
                need_description=draft["need_description"],
            )
            summary = summary_model.model_dump(mode="json")
            expires_at = now + timedelta(minutes=self.hold_minutes)
            preparation = connection.execute(
                booking_preparations.insert()
                .values(
                    id=preparation_id,
                    operation_id=operation_id,
                    draft_id=draft["id"],
                    merchant_id=context.merchant_id,
                    owner_id=context.owner_id,
                    voice_session_id=context.voice_session_id,
                    candidate_id=candidate_id,
                    staff_id=candidate["staff_id"],
                    service_id=candidate["service_id"],
                    draft_version=prepared_version,
                    availability_revision=draft["availability_revision"],
                    schedule_revision=merchant["schedule_revision"],
                    starts_at=candidate["starts_at"],
                    ends_at=candidate["ends_at"],
                    busy_starts_at=candidate["busy_starts_at"],
                    busy_ends_at=candidate["busy_ends_at"],
                    merchant_time_zone=merchant["time_zone"],
                    summary=summary,
                    summary_digest=self._summary_digest(summary),
                    status="prepared",
                    prepared_turn_id=context.turn_id,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                .returning(booking_preparations)
            ).mappings().one()
            connection.execute(
                booking_drafts.update()
                .where(booking_drafts.c.id == draft["id"])
                .values(
                    selected_candidate_id=candidate_id,
                    pending_preparation_id=preparation_id,
                    version=prepared_version,
                    updated_at=func.now(),
                )
            )
        return BookingOperationResult(
            operation_id=operation_id,
            status="prepared",
            preparation=self._preparation_model(preparation),
            context=context,
        )

    def mark_presented(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        *,
        expected_draft_version: int,
        presentation_id: str,
    ) -> BookingOperationResult:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    select(merchants.c.id)
                    .where(merchants.c.id == context.merchant_id)
                    .with_for_update()
                ).scalar_one_or_none()
                preparation = connection.execute(
                    select(booking_preparations)
                    .where(
                        booking_preparations.c.id == preparation_id,
                        *self._identity(context),
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                if preparation is None:
                    raise BookingPreparationNotFoundError(str(preparation_id))
                now = self.now_provider()
                preparation = self._expire_row(connection, preparation, now)
                if preparation["status"] == "expired":
                    raise _ExpiryDetected
                draft = connection.execute(
                    self._draft_statement(context).with_for_update()
                ).mappings().one()
                if (
                    draft["version"] != expected_draft_version
                    or preparation["draft_version"] != expected_draft_version
                    or draft["pending_preparation_id"] != preparation_id
                ):
                    raise BookingConfirmationConflictError("draft_version_changed")
                if preparation["status"] == "presented":
                    if preparation["presentation_id"] != presentation_id:
                        raise BookingConfirmationConflictError("different_presentation")
                elif preparation["status"] != "prepared":
                    raise BookingConfirmationConflictError(preparation["status"])
                else:
                    preparation = connection.execute(
                        booking_preparations.update()
                        .where(booking_preparations.c.id == preparation_id)
                        .values(
                            status="presented",
                            presentation_id=presentation_id,
                            presented_at=now,
                            updated_at=now,
                        )
                        .returning(booking_preparations)
                    ).mappings().one()
        except _ExpiryDetected:
            self._persist_expiration(context, preparation_id)
            raise BookingPreparationExpiredError(str(preparation_id)) from None
        return BookingOperationResult(
            operation_id=preparation["operation_id"],
            status="presented",
            preparation=self._preparation_model(preparation),
            context=context,
        )

    def confirm(
        self,
        context: VoiceRequestContext,
        preparation_id: UUID,
        *,
        expected_draft_version: int,
        confirmation_event_id: str,
    ) -> BookingOperationResult:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    select(merchants.c.id)
                    .where(merchants.c.id == context.merchant_id)
                    .with_for_update()
                ).scalar_one()
                preparation = connection.execute(
                    select(booking_preparations)
                    .where(
                        booking_preparations.c.id == preparation_id,
                        *self._identity(context),
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                if preparation is None:
                    raise BookingPreparationNotFoundError(str(preparation_id))
                existing = connection.execute(
                    select(bookings)
                    .where(bookings.c.preparation_id == preparation_id)
                    .with_for_update()
                ).mappings().one_or_none()
                if existing is not None:
                    existing = expire_unpaid_booking(
                        connection, existing, self.now_provider()
                    )
                    return BookingOperationResult(
                        operation_id=existing["operation_id"],
                        status=existing["status"],
                        preparation=self._preparation_model(preparation),
                        booking=self._booking_model(existing),
                        customer_credential=self._credential(existing),
                        context=context,
                    )

                now = self.now_provider()
                self._expire_merchant_payment_holds(
                    connection, context.merchant_id, now
                )
                preparation = self._expire_row(
                    connection,
                    preparation,
                    now,
                    force=preparation["starts_at"] <= now,
                )
                if preparation["status"] == "expired":
                    raise _ExpiryDetected
                if preparation["status"] in {"cancelled", "confirmed"}:
                    raise BookingConfirmationConflictError(preparation["status"])
                if (
                    preparation["status"] != "presented"
                    or not preparation["presentation_id"]
                ):
                    raise BookingPreparationNotPresentedError(str(preparation_id))
                if context.turn_id == preparation["prepared_turn_id"]:
                    raise BookingConfirmationConflictError(
                        "confirmation_requires_new_turn"
                    )
                draft = connection.execute(
                    self._draft_statement(context).with_for_update()
                ).mappings().one()
                if (
                    expected_draft_version != preparation["draft_version"]
                    or draft["version"] != preparation["draft_version"]
                    or draft["pending_preparation_id"] != preparation_id
                ):
                    raise BookingConfirmationConflictError("draft_version_changed")
                reused_event = connection.execute(
                    select(booking_preparations.c.id).where(
                        booking_preparations.c.merchant_id == context.merchant_id,
                        booking_preparations.c.confirmation_event_id
                        == confirmation_event_id,
                        booking_preparations.c.id != preparation_id,
                    )
                ).scalar_one_or_none()
                if reused_event is not None:
                    raise BookingConfirmationConflictError("confirmation_event_reused")
                schedule_revision = connection.execute(
                    select(merchants.c.schedule_revision).where(
                        merchants.c.id == context.merchant_id
                    )
                ).scalar_one()
                if schedule_revision != preparation["schedule_revision"]:
                    raise BookingConfirmationConflictError(
                        "merchant_schedule_changed"
                    )
                conflict = connection.execute(
                    select(bookings.c.id)
                    .where(
                        bookings.c.merchant_id == context.merchant_id,
                        active_booking_condition(now),
                        _overlaps(
                            bookings,
                            staff_id=preparation["staff_id"],
                            busy_start=preparation["busy_starts_at"],
                            busy_end=preparation["busy_ends_at"],
                        ),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if conflict is not None:
                    raise CandidateUnavailableError("candidate_conflict")

                booking_id = uuid4()
                customer_reference = f"BK-{booking_id.hex[:12].upper()}"
                token = self._credential_token(booking_id)
                payment_expires_at = now + timedelta(
                    minutes=self.payment_hold_minutes
                )
                booking = connection.execute(
                    bookings.insert()
                    .values(
                        id=booking_id,
                        preparation_id=preparation_id,
                        operation_id=preparation["operation_id"],
                        draft_id=draft["id"],
                        merchant_id=context.merchant_id,
                        owner_id=context.owner_id,
                        voice_session_id=context.voice_session_id,
                        candidate_id=preparation["candidate_id"],
                        staff_id=preparation["staff_id"],
                        service_id=preparation["service_id"],
                        customer_name=draft["customer_name"],
                        contact_phone=draft["contact_phone"],
                        pet_name=draft["pet_name"],
                        pet_species=draft["pet_species"],
                        pet_breed=draft["pet_breed"],
                        pet_age_years=draft["pet_age_years"],
                        pet_care_notes=draft["pet_care_notes"],
                        need_description=draft["need_description"],
                        starts_at=preparation["starts_at"],
                        ends_at=preparation["ends_at"],
                        busy_starts_at=preparation["busy_starts_at"],
                        busy_ends_at=preparation["busy_ends_at"],
                        merchant_time_zone=preparation["merchant_time_zone"],
                        summary=preparation["summary"],
                        summary_digest=preparation["summary_digest"],
                        confirmation_event_id=confirmation_event_id,
                        customer_reference=customer_reference,
                        confirmation_code=None,
                        customer_access_token_hash=self._token_hash(token),
                        payment_expires_at=payment_expires_at,
                        status="awaiting_payment",
                        created_at=now,
                    )
                    .returning(bookings)
                ).mappings().one()
                connection.execute(
                    booking_preparations.update()
                    .where(booking_preparations.c.id == preparation_id)
                    .values(
                        status="confirmed",
                        confirmation_event_id=confirmation_event_id,
                        updated_at=now,
                    )
                )
                connection.execute(
                    booking_drafts.update()
                    .where(booking_drafts.c.id == draft["id"])
                    .values(
                        booking_id=booking_id,
                        pending_preparation_id=None,
                        version=draft["version"] + 1,
                        updated_at=now,
                    )
                )
        except _ExpiryDetected:
            self._persist_expiration(context, preparation_id)
            raise BookingPreparationExpiredError(str(preparation_id)) from None
        return BookingOperationResult(
            operation_id=booking["operation_id"],
            status="awaiting_payment",
            booking=self._booking_model(booking),
            customer_credential=self._credential(booking),
            context=context,
        )

    def get_status(
        self,
        context: VoiceRequestContext,
        operation_id: UUID,
    ) -> BookingOperationResult:
        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == context.merchant_id)
                .with_for_update()
            ).scalar_one_or_none()
            preparation = connection.execute(
                select(booking_preparations)
                .where(
                    booking_preparations.c.operation_id == operation_id,
                    *self._identity(context),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if preparation is None:
                raise BookingPreparationNotFoundError(str(operation_id))
            now = self.now_provider()
            preparation = self._expire_row(connection, preparation, now)
            booking = connection.execute(
                select(bookings)
                .where(bookings.c.operation_id == operation_id)
                .with_for_update()
            ).mappings().one_or_none()
            if booking is not None:
                booking = expire_unpaid_booking(connection, booking, now)
        return BookingOperationResult(
            operation_id=operation_id,
            status=booking["status"] if booking is not None else preparation["status"],
            preparation=self._preparation_model(preparation),
            booking=self._booking_model(booking) if booking is not None else None,
            customer_credential=(
                self._credential(booking) if booking is not None else None
            ),
            context=context,
        )

    def _customer_booking_row(
        self,
        customer_reference: str,
        access_token: str,
    ):
        with self.engine.connect() as connection:
            identity = connection.execute(
                select(bookings.c.merchant_id).where(
                    bookings.c.customer_reference == customer_reference
                )
            ).scalar_one_or_none()
        if identity is None:
            raise BookingCredentialError("invalid booking credential")
        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == identity)
                .with_for_update()
            ).scalar_one()
            booking = connection.execute(
                select(bookings)
                .where(bookings.c.customer_reference == customer_reference)
                .with_for_update()
            ).mappings().one_or_none()
            if booking is None or not hmac.compare_digest(
                booking["customer_access_token_hash"],
                self._token_hash(access_token),
            ):
                raise BookingCredentialError("invalid booking credential")
            booking = expire_unpaid_booking(
                connection, booking, self.now_provider()
            )
        return booking

    def get_customer_booking(
        self, customer_reference: str, access_token: str
    ) -> BookingRecord:
        return self._booking_model(
            self._customer_booking_row(customer_reference, access_token)
        )

    def simulate_payment(
        self,
        customer_reference: str,
        access_token: str,
        payment_event_id: str,
    ) -> BookingRecord:
        expired = False
        with self.engine.connect() as connection:
            identity = connection.execute(
                select(bookings.c.merchant_id).where(
                    bookings.c.customer_reference == customer_reference
                )
            ).scalar_one_or_none()
        if identity is None:
            raise BookingCredentialError("invalid booking credential")

        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == identity)
                .with_for_update()
            ).scalar_one()
            booking = connection.execute(
                select(bookings)
                .where(bookings.c.customer_reference == customer_reference)
                .with_for_update()
            ).mappings().one_or_none()
            if booking is None or not hmac.compare_digest(
                booking["customer_access_token_hash"],
                self._token_hash(access_token),
            ):
                raise BookingCredentialError("invalid booking credential")
            now = self.now_provider()
            booking = expire_unpaid_booking(connection, booking, now)
            if booking["status"] == "payment_expired":
                expired = True
            elif booking["status"] == "confirmed":
                pass
            elif booking["status"] != "awaiting_payment":
                raise BookingPaymentConflictError(booking["status"])
            else:
                reused_event = connection.execute(
                    select(bookings.c.id).where(
                        bookings.c.merchant_id == booking["merchant_id"],
                        bookings.c.payment_event_id == payment_event_id,
                        bookings.c.id != booking["id"],
                    )
                ).scalar_one_or_none()
                if reused_event is not None:
                    raise BookingPaymentConflictError("payment_event_reused")
                confirmation_code = f"PR-{booking['id'].hex[:12].upper()}"
                booking = connection.execute(
                    bookings.update()
                    .where(
                        bookings.c.id == booking["id"],
                        bookings.c.status == "awaiting_payment",
                    )
                    .values(
                        status="confirmed",
                        confirmation_code=confirmation_code,
                        paid_at=now,
                        payment_event_id=payment_event_id,
                        payment_method="local_simulation",
                    )
                    .returning(bookings)
                ).mappings().one()
        if expired:
            raise BookingPaymentExpiredError(customer_reference)
        return self._booking_model(booking)

    def list_merchant_bookings(self, merchant_id: str) -> MerchantBookingList:
        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == merchant_id)
                .with_for_update()
            ).scalar_one_or_none()
            self._expire_merchant_payment_holds(
                connection, merchant_id, self.now_provider()
            )
            rows = list(
                connection.execute(
                    select(bookings)
                    .where(bookings.c.merchant_id == merchant_id)
                    .order_by(bookings.c.created_at.desc())
                ).mappings()
            )
        return MerchantBookingList(
            merchant_id=merchant_id,
            bookings=[self._booking_model(row) for row in rows],
        )

    def get_merchant_booking(self, merchant_id: str, booking_id: UUID) -> BookingRecord:
        with self.engine.begin() as connection:
            connection.execute(
                select(merchants.c.id)
                .where(merchants.c.id == merchant_id)
                .with_for_update()
            ).scalar_one_or_none()
            row = connection.execute(
                select(bookings)
                .where(
                    bookings.c.id == booking_id,
                    bookings.c.merchant_id == merchant_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if row is not None:
                row = expire_unpaid_booking(connection, row, self.now_provider())
        if row is None:
            raise BookingPreparationNotFoundError(str(booking_id))
        return self._booking_model(row)
