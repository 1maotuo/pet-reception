from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID


metadata = MetaData()

merchants = Table(
    "merchants",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("display_name", String(200), nullable=False),
    Column("merchant_type", String(32), nullable=False),
    Column("time_zone", String(80), nullable=False),
    Column("phone", String(40), nullable=False),
    Column("address", String(500), nullable=False),
    Column("is_demo", Boolean, nullable=False, server_default="true"),
    Column("source", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "merchant_type IN ('grooming', 'hospital', 'daycare')",
        name="ck_merchants_type",
    ),
)

merchant_services = Table(
    "merchant_services",
    metadata,
    Column("merchant_id", String(128), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("name_zh", String(200), nullable=False),
    Column("duration_minutes", Integer, nullable=False),
    Column("price_note", String(500), nullable=False),
    Column("display_order", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["merchant_id"],
        ["merchants.id"],
        ondelete="CASCADE",
        name="fk_merchant_services_merchant",
    ),
    CheckConstraint("duration_minutes > 0", name="ck_services_positive_duration"),
    CheckConstraint("display_order >= 0", name="ck_services_display_order"),
)

booking_drafts = Table(
    "booking_drafts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("merchant_id", String(128), nullable=False),
    Column("owner_id", String(128), nullable=False),
    Column("voice_session_id", String(128), nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("customer_name", String(200)),
    Column("contact_phone", String(40)),
    Column("contact_phone_source", String(32)),
    Column("incoming_phone", String(40)),
    Column("pet_name", String(200)),
    Column("pet_species", String(80)),
    Column("pet_breed", String(200)),
    Column("pet_age_years", Numeric(4, 1)),
    Column("pet_care_notes", Text),
    Column("need_description", Text),
    Column("service_id", String(128)),
    Column("preferred_date", Date),
    Column("preferred_time_of_day", String(32)),
    Column("staff_preference_text", String(200)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "merchant_id",
        "owner_id",
        "voice_session_id",
        name="uq_booking_drafts_trusted_session",
    ),
    ForeignKeyConstraint(
        ["merchant_id"],
        ["merchants.id"],
        ondelete="RESTRICT",
        name="fk_booking_drafts_merchant",
    ),
    ForeignKeyConstraint(
        ["merchant_id", "service_id"],
        ["merchant_services.merchant_id", "merchant_services.id"],
        ondelete="RESTRICT",
        name="fk_booking_drafts_service",
    ),
    CheckConstraint("version >= 0", name="ck_booking_drafts_version"),
    CheckConstraint(
        "contact_phone_source IS NULL OR contact_phone_source IN "
        "('voice_user_provided', 'web_user_provided')",
        name="ck_booking_drafts_contact_source",
    ),
    CheckConstraint(
        "preferred_time_of_day IS NULL OR preferred_time_of_day IN "
        "('morning', 'afternoon', 'evening', 'flexible')",
        name="ck_booking_drafts_time_of_day",
    ),
    CheckConstraint(
        "pet_age_years IS NULL OR (pet_age_years >= 0 AND pet_age_years <= 50)",
        name="ck_booking_drafts_pet_age",
    ),
)
