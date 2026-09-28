from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .business_rules import (
    normalize_contact_phone,
    normalize_pet_species,
    parse_pet_age_expression,
    validate_pet_name,
)


class DatabaseStatus(BaseModel):
    status: Literal["connected", "unavailable", "not_configured"]
    detail: str


class SystemStatus(BaseModel):
    service: Literal["pet-reception-backend"] = "pet-reception-backend"
    status: Literal["ok", "degraded"]
    database: DatabaseStatus


class StructuredPrice(BaseModel):
    """One configured monetary fact, always expressed in integer minor units."""

    model_config = ConfigDict(extra="forbid")

    semantics: Literal["fixed", "starting_at", "range", "assessment_required"]
    amount_cents: int | None = Field(default=None, ge=0)
    maximum_amount_cents: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_amounts_matching_semantics(self) -> "StructuredPrice":
        if self.semantics in {"fixed", "starting_at"}:
            if self.amount_cents is None or self.maximum_amount_cents is not None:
                raise ValueError(
                    f"{self.semantics} price requires amount_cents only"
                )
        elif self.semantics == "range":
            if (
                self.amount_cents is None
                or self.maximum_amount_cents is None
                or self.maximum_amount_cents <= self.amount_cents
            ):
                raise ValueError(
                    "range price requires maximum_amount_cents greater than amount_cents"
                )
        elif self.amount_cents is not None or self.maximum_amount_cents is not None:
            raise ValueError("assessment_required price cannot contain an amount")
        return self


class ServiceItemConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)


class ServiceAddonConfiguration(ServiceItemConfiguration):
    enabled: bool = True
    supported_species: list[str] = Field(default_factory=list)
    price: StructuredPrice | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    requires_assessment: bool = False
    assessment_reason: str | None = Field(default=None, max_length=500)
    source: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_assessment_reason(self) -> "ServiceAddonConfiguration":
        if self.requires_assessment and not self.assessment_reason:
            raise ValueError("assessment_reason is required when assessment is required")
        return self


class WeightTierConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    minimum_weight: Decimal = Field(ge=0)
    maximum_weight: Decimal | None = Field(default=None, gt=0)
    unit: Literal["lb", "kg"]
    minimum_inclusive: bool
    maximum_inclusive: bool
    price: StructuredPrice
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    eligible: bool = True
    assessment_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_valid_interval(self) -> "WeightTierConfiguration":
        if self.maximum_weight is not None and self.minimum_weight >= self.maximum_weight:
            raise ValueError("weight tier minimum must be below maximum")
        if self.maximum_weight is None and self.maximum_inclusive:
            raise ValueError("an unbounded maximum cannot be inclusive")
        if not self.eligible and not self.assessment_reason:
            raise ValueError("ineligible weight tiers require assessment_reason")
        return self


class ServiceConfiguration(BaseModel):
    """Optional structured rules for one merchant service.

    The legacy service duration and price note remain authoritative when this
    configuration is absent. This object is deliberately small rather than a
    generic pricing/rules language.
    """

    model_config = ConfigDict(extra="forbid")

    service_kind: Literal["package", "standalone"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    included_items: list[ServiceItemConfiguration] = Field(default_factory=list)
    addons: list[ServiceAddonConfiguration] = Field(default_factory=list)
    base_price: StructuredPrice | None = None
    weight_tiers: list[WeightTierConfiguration] = Field(default_factory=list)
    weight_usage: Literal[
        "not_used",
        "price_only",
        "price_and_duration",
        "eligibility",
    ] = "not_used"
    source: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_consistent_rules(self) -> "ServiceConfiguration":
        included_ids = [item.id for item in self.included_items]
        addon_ids = [item.id for item in self.addons]
        if len(set(included_ids)) != len(included_ids):
            raise ValueError("included item ids must be unique")
        if len(set(addon_ids)) != len(addon_ids):
            raise ValueError("addon ids must be unique within a service")
        overlap = set(included_ids).intersection(addon_ids)
        if overlap:
            raise ValueError(
                f"included items cannot also be configured as add-ons: {sorted(overlap)}"
            )
        if self.base_price is not None and self.weight_tiers:
            raise ValueError("base_price and weight_tiers are mutually exclusive")
        if self.weight_tiers and self.weight_usage == "not_used":
            raise ValueError("weight_usage must describe configured weight tiers")
        if not self.weight_tiers and self.weight_usage != "not_used":
            raise ValueError("weight_usage requires weight_tiers")
        if self.weight_usage == "price_and_duration" and any(
            tier.duration_minutes is None for tier in self.weight_tiers
        ):
            raise ValueError(
                "price_and_duration tiers must all configure duration_minutes"
            )
        self._validate_weight_tier_partition()
        return self

    def _validate_weight_tier_partition(self) -> None:
        if not self.weight_tiers:
            return
        pounds_to_kg = Decimal("0.45359237")

        def in_kg(value: Decimal | None, unit: str) -> Decimal | None:
            if value is None:
                return None
            return value if unit == "kg" else value * pounds_to_kg

        ordered = sorted(
            self.weight_tiers,
            key=lambda tier: in_kg(tier.minimum_weight, tier.unit) or Decimal(0),
        )
        first_minimum = in_kg(ordered[0].minimum_weight, ordered[0].unit)
        if first_minimum != 0 or not ordered[0].minimum_inclusive:
            raise ValueError("weight tiers must start at an inclusive zero boundary")
        for previous, current in zip(ordered, ordered[1:]):
            previous_maximum = in_kg(previous.maximum_weight, previous.unit)
            current_minimum = in_kg(current.minimum_weight, current.unit)
            if previous_maximum is None or previous_maximum != current_minimum:
                raise ValueError("weight tiers must be contiguous and non-overlapping")
            if previous.maximum_inclusive == current.minimum_inclusive:
                raise ValueError(
                    "exactly one adjacent tier must include the shared boundary"
                )
        if ordered[-1].maximum_weight is not None:
            raise ValueError("weight tiers must include one unbounded final tier")


class MerchantService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name_zh: str
    duration_minutes: int = Field(gt=0, le=1440)
    price_note: str
    supported_species: list[str] = Field(default_factory=list)
    included_items: list[str] = Field(default_factory=list)
    recommended_for: list[str] = Field(default_factory=list)
    assessment_required_for: list[str] = Field(default_factory=list)
    not_included: list[str] = Field(default_factory=list)
    source: str | None = None
    # Internal catalog fact. Callers receive only the calculated quote, never
    # the raw rule set through the existing merchant-info/model tool.
    service_configuration: ServiceConfiguration | None = Field(default=None, exclude=True)


class QuoteWeightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Decimal | None = Field(default=None, gt=0)
    minimum: Decimal | None = Field(default=None, gt=0)
    maximum: Decimal | None = Field(default=None, gt=0)
    unit: Literal["lb", "kg"]
    estimated: bool = False

    @model_validator(mode="after")
    def require_point_or_range(self) -> "QuoteWeightInput":
        has_value = self.value is not None
        has_range = self.minimum is not None or self.maximum is not None
        if has_value == has_range:
            raise ValueError("provide either value or minimum/maximum weight")
        if has_range:
            if self.minimum is None or self.maximum is None:
                raise ValueError("weight range requires both minimum and maximum")
            if self.minimum > self.maximum:
                raise ValueError("weight minimum cannot exceed maximum")
        return self


class ServiceQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    pet_species: str | None = Field(default=None, min_length=1, max_length=80)
    weight: QuoteWeightInput | None = None
    selected_addon_ids: list[str] = Field(default_factory=list, max_length=32)


class QuoteLineItem(BaseModel):
    item_id: str
    display_name: str
    kind: Literal["base_service", "addon", "included_selection"]
    pricing_semantics: Literal[
        "fixed",
        "starting_at",
        "range",
        "assessment_required",
        "unconfigured",
    ]
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    duration_minutes: int | None = None
    note: str | None = None


class ServiceQuoteResult(BaseModel):
    merchant_id: str
    service_id: str
    service_name: str
    service_kind: Literal["package", "standalone"] | None
    applicable_species: list[str]
    requested_species: str | None
    weight: QuoteWeightInput | None
    matched_weight_tier_id: str | None
    included_items: list[ServiceItemConfiguration]
    selected_addon_ids: list[str]
    duplicate_addon_ids: list[str]
    line_items: list[QuoteLineItem]
    currency: str | None
    total_semantics: Literal[
        "fixed",
        "starting_at",
        "range",
        "assessment_required",
        "unconfigured",
    ]
    total_min_cents: int | None
    total_max_cents: int | None
    legacy_price_note: str
    scheduling_duration_minutes: int | None
    missing_for_quote: list[str]
    missing_for_availability: list[str]
    missing_for_booking: list[str]
    requires_assessment: bool
    assessment_reasons: list[str]
    can_quote: bool
    can_schedule: bool
    source: str | None


class GroomingPolicy(BaseModel):
    supported_species: list[str] = Field(min_length=1)
    unsupported_species: list[str] = Field(default_factory=list)
    staff_assessment_required_for: list[str] = Field(default_factory=list)
    recommendation_boundary: str
    medical_boundary: str
    minimum_age_note: str | None = None
    vaccination_note: str | None = None
    arrival_note: str | None = None
    late_arrival_note: str | None = None
    aggression_policy: str | None = None
    price_confirmation_policy: str | None = None


class PublicBusinessHoursWindow(BaseModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def require_forward_window(self) -> "PublicBusinessHoursWindow":
        if self.opens_at.tzinfo is not None or self.closes_at.tzinfo is not None:
            raise ValueError("business hour times must be local wall times without offsets")
        if self.opens_at >= self.closes_at:
            raise ValueError("business hour opens_at must be before closes_at")
        return self


class MerchantBusinessHours(BaseModel):
    weekly_hours: list[PublicBusinessHoursWindow] = Field(min_length=1)
    closed_weekdays: list[int] = Field(default_factory=list)
    note: str
    source: str

    @model_validator(mode="after")
    def require_consistent_weekdays(self) -> "MerchantBusinessHours":
        closed = set(self.closed_weekdays)
        if len(closed) != len(self.closed_weekdays):
            raise ValueError("closed_weekdays must be unique")
        if any(day < 0 or day > 6 for day in closed):
            raise ValueError("closed weekdays must be between 0 and 6")
        open_days = {window.weekday for window in self.weekly_hours}
        if open_days.intersection(closed):
            raise ValueError("a weekday cannot be both open and closed")
        by_weekday: dict[int, list[PublicBusinessHoursWindow]] = {}
        for window in self.weekly_hours:
            by_weekday.setdefault(window.weekday, []).append(window)
        for windows in by_weekday.values():
            ordered = sorted(windows, key=lambda item: item.opens_at)
            for previous, current in zip(ordered, ordered[1:]):
                if current.opens_at < previous.closes_at:
                    raise ValueError("business hour windows must not overlap")
        return self


class MerchantStaff(BaseModel):
    id: str
    display_name: str
    service_ids: list[str]
    public_intro: str | None = None
    specialties: list[str] = Field(default_factory=list)
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
    grooming_policy: GroomingPolicy | None = None
    business_hours: MerchantBusinessHours | None = None
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
    public_intro: str | None = None
    specialties: list[str] = Field(default_factory=list)
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


class ServiceQuoteToolResult(BaseModel):
    quote: ServiceQuoteResult
    context: VoiceRequestContext


class MerchantToolResult(BaseModel):
    merchant: DemoMerchant
    merchant_local_date: date
    merchant_local_datetime: datetime
    context: VoiceRequestContext


DraftFieldName = Literal[
    "customer_name",
    "contact_phone",
    "pet_name",
    "pet_species",
    "pet_breed",
    "pet_age_years",
    "pet_age_months",
    "pet_age_text",
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
    "pet_age_months",
    "pet_age_text",
    "pet_care_notes",
    "need_description",
    "service_id",
    "preferred_date",
    "preferred_time_of_day",
    "staff_preference_text",
    "staff_id",
)

AGE_DRAFT_FIELDS = frozenset(
    {"pet_age_years", "pet_age_months", "pet_age_text"}
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
    pet_age_months: int | None = Field(default=None, ge=0, le=600)
    pet_age_text: str | None = Field(default=None, max_length=80)
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
        "pet_age_text",
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

    @field_validator("contact_phone")
    @classmethod
    def require_real_contact_phone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_contact_phone(value)

    @field_validator("pet_name")
    @classmethod
    def reject_placeholder_pet_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_pet_name(value)

    @field_validator("pet_species")
    @classmethod
    def normalize_species_alias(cls, value: str | None) -> str | None:
        return normalize_pet_species(value) if value is not None else None

    @field_validator("pet_age_text")
    @classmethod
    def require_supported_age_expression(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return parse_pet_age_expression(value).display_text

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
        age_inputs = provided.intersection(AGE_DRAFT_FIELDS)
        if len(age_inputs) > 1:
            raise ValueError("provide pet age through only one age representation")
        return self

    def changes(self) -> dict[DraftFieldName, object]:
        provided = set(self.model_fields_set).intersection(DRAFT_MUTABLE_FIELDS)
        changes = {name: getattr(self, name) for name in provided}
        changes.update({name: None for name in self.clear_fields})
        if "pet_age_text" in provided:
            age = parse_pet_age_expression(self.pet_age_text or "")
            changes.update(
                pet_age_text=age.display_text,
                pet_age_months=age.months,
                pet_age_years=age.years,
            )
        elif "pet_age_months" in provided:
            changes.update(pet_age_text=None, pet_age_years=None)
        elif "pet_age_years" in provided:
            changes.update(pet_age_text=None, pet_age_months=None)
        if not provided.intersection(AGE_DRAFT_FIELDS) and self.clear_fields.intersection(
            AGE_DRAFT_FIELDS
        ):
            changes.update(pet_age_text=None, pet_age_months=None, pet_age_years=None)
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
    pet_age_months: int | None = None
    pet_age_text: str | None = None
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


class StartBookingSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_voice_session_id: str | None = Field(default=None, max_length=128)


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
    pet_age_months: int | None = None
    pet_age_text: str | None = None
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
    pet_age_months: int | None
    pet_age_text: str | None
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
