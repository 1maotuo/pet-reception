from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DatabaseStatus(BaseModel):
    status: Literal["connected", "unavailable", "not_configured"]
    detail: str


class SystemStatus(BaseModel):
    service: Literal["pet-reception-backend"] = "pet-reception-backend"
    status: Literal["ok", "degraded"]
    database: DatabaseStatus


class MerchantService(BaseModel):
    id: str
    name_zh: str
    duration_minutes: int = Field(gt=0, le=1440)
    price_note: str


class MerchantStaff(BaseModel):
    id: str
    display_name: str
    service_ids: list[str]
    source: str


class DemoMerchant(BaseModel):
    id: str
    display_name: str
    merchant_type: Literal["grooming", "hospital", "daycare"]
    time_zone: str
    phone: str
    address: str
    services: list[MerchantService]
    staff: list[MerchantStaff] = Field(default_factory=list)
    is_demo: Literal[True] = True
    source: str

    @field_validator("time_zone")
    @classmethod
    def require_iana_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("time_zone must be a valid IANA zone") from error
        return value


class ServiceBufferSeed(BaseModel):
    service_id: str
    buffer_before_minutes: int = Field(ge=0, le=120)
    buffer_after_minutes: int = Field(ge=0, le=120)


class WeeklyWindowSeed(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def require_forward_window(self) -> "WeeklyWindowSeed":
        if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
            raise ValueError("working window times must be local wall times without offsets")
        if self.start_time >= self.end_time:
            raise ValueError("working window start_time must be before end_time")
        return self


class StaffSeed(BaseModel):
    id: str
    display_name: str
    service_ids: list[str] = Field(min_length=1)
    weekly_hours: list[WeeklyWindowSeed] = Field(min_length=1)
    source: str

    @model_validator(mode="after")
    def require_unique_services_and_non_overlapping_hours(self) -> "StaffSeed":
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("staff service_ids must be unique")
        by_weekday: dict[int, list[WeeklyWindowSeed]] = {}
        for window in self.weekly_hours:
            by_weekday.setdefault(window.weekday, []).append(window)
        for windows in by_weekday.values():
            ordered = sorted(windows, key=lambda item: item.start_time)
            for previous, current in zip(ordered, ordered[1:]):
                if current.start_time < previous.end_time:
                    raise ValueError("staff working windows must not overlap")
        return self


class MerchantSchedulingSeed(BaseModel):
    slot_interval_minutes: int = Field(default=30, ge=5, le=120)
    candidate_limit: int = Field(default=8, ge=1, le=24)
    alternative_days: int = Field(default=7, ge=1, le=30)
    service_buffers: list[ServiceBufferSeed]
    staff: list[StaffSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_seed_ids(self) -> "MerchantSchedulingSeed":
        buffer_ids = [item.service_id for item in self.service_buffers]
        if len(set(buffer_ids)) != len(buffer_ids):
            raise ValueError("service buffer ids must be unique")
        staff_ids = [item.id for item in self.staff]
        if len(set(staff_ids)) != len(staff_ids):
            raise ValueError("staff ids must be unique")
        return self


class DemoMerchantConfig(DemoMerchant):
    scheduling: MerchantSchedulingSeed | None = None


class VoiceRequestContext(BaseModel):
    merchant_id: str
    owner_id: str
    voice_session_id: str
    turn_id: str
    source: Literal["trusted_gateway_headers"] = "trusted_gateway_headers"


class MerchantToolResult(BaseModel):
    merchant: DemoMerchant
    context: VoiceRequestContext


DraftFieldName = Literal[
    "customer_name",
    "contact_phone",
    "pet_name",
    "pet_species",
    "pet_breed",
    "pet_age_years",
    "pet_care_notes",
    "need_description",
    "service_id",
    "preferred_date",
    "preferred_time_of_day",
    "staff_preference_text",
    "staff_id",
]

DRAFT_MUTABLE_FIELDS: tuple[DraftFieldName, ...] = (
    "customer_name",
    "contact_phone",
    "pet_name",
    "pet_species",
    "pet_breed",
    "pet_age_years",
    "pet_care_notes",
    "need_description",
    "service_id",
    "preferred_date",
    "preferred_time_of_day",
    "staff_preference_text",
    "staff_id",
)


class BookingDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    clear_fields: set[DraftFieldName] = Field(default_factory=set)
    customer_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=40)
    pet_name: str | None = Field(default=None, max_length=200)
    pet_species: str | None = Field(default=None, max_length=80)
    pet_breed: str | None = Field(default=None, max_length=200)
    pet_age_years: Decimal | None = Field(default=None, ge=0, le=50, decimal_places=1)
    pet_care_notes: str | None = Field(default=None, max_length=2000)
    need_description: str | None = Field(default=None, max_length=2000)
    service_id: str | None = Field(default=None, max_length=128)
    preferred_date: date | None = None
    preferred_time_of_day: Literal[
        "morning",
        "afternoon",
        "evening",
        "flexible",
    ] | None = None
    staff_preference_text: str | None = Field(default=None, max_length=200)
    staff_id: str | None = Field(default=None, max_length=128)

    @field_validator(
        "customer_name",
        "contact_phone",
        "pet_name",
        "pet_species",
        "pet_breed",
        "pet_care_notes",
        "need_description",
        "service_id",
        "staff_preference_text",
        "staff_id",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("empty text is not a clear operation; use clear_fields")
        return normalized

    @model_validator(mode="after")
    def require_explicit_changes(self) -> "BookingDraftPatch":
        provided = set(self.model_fields_set).intersection(DRAFT_MUTABLE_FIELDS)
        null_fields = {name for name in provided if getattr(self, name) is None}
        if null_fields:
            fields = ", ".join(sorted(null_fields))
            raise ValueError(f"null is not a clear operation; use clear_fields for: {fields}")
        overlap = provided.intersection(self.clear_fields)
        if overlap:
            fields = ", ".join(sorted(overlap))
            raise ValueError(f"fields cannot be set and cleared together: {fields}")
        if not provided and not self.clear_fields:
            raise ValueError("at least one draft field must be set or cleared")
        return self

    def changes(self) -> dict[DraftFieldName, object]:
        provided = set(self.model_fields_set).intersection(DRAFT_MUTABLE_FIELDS)
        changes = {name: getattr(self, name) for name in provided}
        changes.update({name: None for name in self.clear_fields})
        return changes


class BookingDraft(BaseModel):
    id: UUID
    merchant_id: str
    owner_id: str
    voice_session_id: str
    version: int
    availability_revision: int
    customer_name: str | None
    contact_phone: str | None
    contact_phone_source: Literal["voice_user_provided", "web_user_provided"] | None
    incoming_phone: str | None
    pet_name: str | None
    pet_species: str | None
    pet_breed: str | None
    pet_age_years: Decimal | None
    pet_care_notes: str | None
    need_description: str | None
    service_id: str | None
    preferred_date: date | None
    preferred_time_of_day: Literal[
        "morning",
        "afternoon",
        "evening",
        "flexible",
    ] | None
    staff_preference_text: str | None
    staff_id: str | None
    selected_candidate_id: UUID | None
    pending_preparation_id: UUID | None
    booking_id: UUID | None
    created_at: datetime
    updated_at: datetime


class BookingDraftToolResult(BaseModel):
    draft: BookingDraft
    context: VoiceRequestContext
    changed_fields: list[DraftFieldName] = Field(default_factory=list)
    missing_for_availability: list[str]
    missing_for_booking: list[str]


class AvailabilitySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_availability_revision: int = Field(ge=0)


class AvailabilityCandidate(BaseModel):
    id: UUID
    staff_id: str
    staff_display_name: str
    service_id: str
    starts_at: datetime
    ends_at: datetime
    busy_starts_at: datetime
    busy_ends_at: datetime
    merchant_time_zone: str


class AlternativeAvailabilityDate(BaseModel):
    date: date
    first_start_at: datetime
    available_staff_ids: list[str]


class AvailabilitySearchResult(BaseModel):
    id: UUID
    status: Literal["available", "no_availability", "stale"]
    is_current: bool
    stale_reason: str | None = None
    draft_id: UUID
    draft_version: int
    availability_revision: int
    schedule_revision: int
    service_id: str
    preferred_date: date
    preferred_time_of_day: Literal[
        "morning",
        "afternoon",
        "evening",
        "flexible",
    ] | None
    staff_id: str | None
    merchant_time_zone: str
    candidates: list[AvailabilityCandidate]
    alternative_dates: list[AlternativeAvailabilityDate]
    source: Literal["postgresql_recurring_staff_schedule"] = (
        "postgresql_recurring_staff_schedule"
    )
    availability_checked: Literal[True] = True
    booking_created: Literal[False] = False
    created_at: datetime


class AvailabilityToolResult(BaseModel):
    search: AvailabilitySearchResult
    context: VoiceRequestContext


class PrepareBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    candidate_id: UUID


class MarkPreparationPresentedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: int = Field(ge=0)


class ConfirmBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: int = Field(ge=0)


class BookingSummary(BaseModel):
    merchant_id: str
    merchant_display_name: str
    service_id: str
    service_name_zh: str
    staff_id: str
    staff_display_name: str
    starts_at: datetime
    ends_at: datetime
    merchant_time_zone: str
    customer_name: str
    contact_phone: str
    pet_name: str
    pet_species: str
    pet_breed: str | None
    pet_age_years: Decimal | None
    pet_care_notes: str | None
    need_description: str | None
    source: Literal["server_booking_snapshot"] = "server_booking_snapshot"


class BookingPreparation(BaseModel):
    id: UUID
    operation_id: UUID
    draft_id: UUID
    draft_version: int
    candidate_id: UUID
    status: Literal["prepared", "presented", "confirmed", "expired", "cancelled"]
    summary: BookingSummary
    summary_digest: str
    presentation_id: str | None
    expires_at: datetime
    availability_held: bool
    booking_created: bool
    created_at: datetime


class BookingPreparationToolResult(BaseModel):
    preparation: BookingPreparation
    context: VoiceRequestContext


class BookingRecord(BaseModel):
    id: UUID
    operation_id: UUID
    preparation_id: UUID
    merchant_id: str
    merchant_display_name: str
    staff_id: str
    staff_display_name: str
    service_id: str
    service_name_zh: str
    customer_name: str
    contact_phone: str
    pet_name: str
    pet_species: str
    pet_breed: str | None
    pet_age_years: Decimal | None
    pet_care_notes: str | None
    need_description: str | None
    starts_at: datetime
    ends_at: datetime
    merchant_time_zone: str
    customer_reference: str
    confirmation_code: str | None
    payment_expires_at: datetime | None
    paid_at: datetime | None
    payment_expired_at: datetime | None
    payment_method: str | None
    status: Literal["awaiting_payment", "confirmed", "payment_expired"]
    created_at: datetime


class CustomerBookingCredential(BaseModel):
    booking_reference: str
    confirmation_code: str | None
    access_token: str
    credential_url: str


class BookingOperationResult(BaseModel):
    operation_id: UUID
    status: Literal[
        "prepared",
        "presented",
        "awaiting_payment",
        "confirmed",
        "payment_expired",
        "expired",
        "cancelled",
    ]
    preparation: BookingPreparation | None = None
    booking: BookingRecord | None = None
    customer_credential: CustomerBookingCredential | None = None
    context: VoiceRequestContext | None = None


class CustomerBookingResult(BaseModel):
    booking: BookingRecord


class MerchantBookingList(BaseModel):
    merchant_id: str
    bookings: list[BookingRecord]
