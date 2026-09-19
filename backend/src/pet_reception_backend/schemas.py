from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

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
    duration_minutes: int
    price_note: str


class DemoMerchant(BaseModel):
    id: str
    display_name: str
    merchant_type: Literal["grooming", "hospital", "daycare"]
    time_zone: str
    phone: str
    address: str
    services: list[MerchantService]
    is_demo: Literal[True] = True
    source: str


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
    created_at: datetime
    updated_at: datetime


class BookingDraftToolResult(BaseModel):
    draft: BookingDraft
    context: VoiceRequestContext
    changed_fields: list[DraftFieldName] = Field(default_factory=list)
    missing_for_availability: list[str]
    missing_for_booking: list[str]
