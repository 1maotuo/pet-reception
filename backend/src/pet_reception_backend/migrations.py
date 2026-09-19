from collections.abc import Callable

from sqlalchemy import Column, DateTime, MetaData, String, Table, func, select, text
from sqlalchemy.engine import Connection, Engine

from .models import metadata


migration_metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    migration_metadata,
    Column("version", String(100), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def create_m2_schema(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id VARCHAR(128) PRIMARY KEY,
                display_name VARCHAR(200) NOT NULL,
                merchant_type VARCHAR(32) NOT NULL,
                time_zone VARCHAR(80) NOT NULL,
                phone VARCHAR(40) NOT NULL,
                address VARCHAR(500) NOT NULL,
                is_demo BOOLEAN NOT NULL DEFAULT TRUE,
                source VARCHAR(200) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_merchants_type
                    CHECK (merchant_type IN ('grooming', 'hospital', 'daycare'))
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS merchant_services (
                merchant_id VARCHAR(128) NOT NULL,
                id VARCHAR(128) NOT NULL,
                name_zh VARCHAR(200) NOT NULL,
                duration_minutes INTEGER NOT NULL,
                price_note VARCHAR(500) NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (merchant_id, id),
                CONSTRAINT fk_merchant_services_merchant
                    FOREIGN KEY (merchant_id) REFERENCES merchants (id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_services_positive_duration
                    CHECK (duration_minutes > 0),
                CONSTRAINT ck_services_display_order CHECK (display_order >= 0)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS booking_drafts (
                id UUID PRIMARY KEY,
                merchant_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(128) NOT NULL,
                voice_session_id VARCHAR(128) NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                customer_name VARCHAR(200),
                contact_phone VARCHAR(40),
                contact_phone_source VARCHAR(32),
                incoming_phone VARCHAR(40),
                pet_name VARCHAR(200),
                pet_species VARCHAR(80),
                pet_breed VARCHAR(200),
                pet_age_years NUMERIC(4, 1),
                pet_care_notes TEXT,
                need_description TEXT,
                service_id VARCHAR(128),
                preferred_date DATE,
                preferred_time_of_day VARCHAR(32),
                staff_preference_text VARCHAR(200),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_booking_drafts_trusted_session
                    UNIQUE (merchant_id, owner_id, voice_session_id),
                CONSTRAINT fk_booking_drafts_merchant
                    FOREIGN KEY (merchant_id) REFERENCES merchants (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_booking_drafts_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_booking_drafts_version CHECK (version >= 0),
                CONSTRAINT ck_booking_drafts_contact_source CHECK (
                    contact_phone_source IS NULL OR contact_phone_source IN
                    ('voice_user_provided', 'web_user_provided')
                ),
                CONSTRAINT ck_booking_drafts_time_of_day CHECK (
                    preferred_time_of_day IS NULL OR preferred_time_of_day IN
                    ('morning', 'afternoon', 'evening', 'flexible')
                ),
                CONSTRAINT ck_booking_drafts_pet_age CHECK (
                    pet_age_years IS NULL OR
                    (pet_age_years >= 0 AND pet_age_years <= 50)
                )
            )
            """
        )
    )


def add_m2_availability_schema(connection: Connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE merchants "
            "ADD COLUMN IF NOT EXISTS slot_interval_minutes INTEGER NOT NULL "
            "DEFAULT 30 CHECK (slot_interval_minutes > 0), "
            "ADD COLUMN IF NOT EXISTS candidate_limit INTEGER NOT NULL "
            "DEFAULT 8 CHECK (candidate_limit > 0), "
            "ADD COLUMN IF NOT EXISTS alternative_days INTEGER NOT NULL "
            "DEFAULT 7 CHECK (alternative_days > 0)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE merchant_services "
            "ADD COLUMN IF NOT EXISTS buffer_before_minutes INTEGER NOT NULL "
            "DEFAULT 0 CHECK (buffer_before_minutes >= 0), "
            "ADD COLUMN IF NOT EXISTS buffer_after_minutes INTEGER NOT NULL "
            "DEFAULT 0 CHECK (buffer_after_minutes >= 0)"
        )
    )

    # Create staff tables before adding the optional staff foreign key to existing drafts.
    for table_name in (
        "staff_members",
        "staff_service_qualifications",
        "staff_working_hours",
    ):
        metadata.tables[table_name].create(connection, checkfirst=True)

    connection.execute(
        text(
            "ALTER TABLE booking_drafts "
            "ADD COLUMN IF NOT EXISTS availability_revision INTEGER NOT NULL "
            "DEFAULT 0 CHECK (availability_revision >= 0), "
            "ADD COLUMN IF NOT EXISTS staff_id VARCHAR(128)"
        )
    )
    connection.execute(
        text(
            "DO $$ BEGIN "
            "IF NOT EXISTS ("
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'fk_booking_drafts_staff'"
            ") THEN "
            "ALTER TABLE booking_drafts ADD CONSTRAINT fk_booking_drafts_staff "
            "FOREIGN KEY (merchant_id, staff_id) "
            "REFERENCES staff_members (merchant_id, id) ON DELETE RESTRICT; "
            "END IF; END $$"
        )
    )

    for table_name in ("availability_searches", "availability_candidates"):
        metadata.tables[table_name].create(connection, checkfirst=True)


def add_m2_schedule_revision(connection: Connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE merchants "
            "ADD COLUMN IF NOT EXISTS schedule_revision INTEGER NOT NULL "
            "DEFAULT 0 CHECK (schedule_revision >= 0), "
            "ADD COLUMN IF NOT EXISTS schedule_fingerprint VARCHAR(64)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE availability_searches "
            "ADD COLUMN IF NOT EXISTS schedule_revision INTEGER NOT NULL DEFAULT 0"
        )
    )


MIGRATIONS: tuple[tuple[str, Callable[[Connection], None]], ...] = (
    ("0001_m2_merchant_and_booking_drafts", create_m2_schema),
    ("0002_m2_staff_and_availability", add_m2_availability_schema),
    ("0003_m2_schedule_revision", add_m2_schedule_revision),
)


def run_migrations(engine: Engine) -> list[str]:
    migration_metadata.create_all(engine)
    applied_now: list[str] = []
    with engine.begin() as connection:
        applied = set(connection.execute(select(schema_migrations.c.version)).scalars())
        for version, migration in MIGRATIONS:
            if version in applied:
                continue
            migration(connection)
            connection.execute(schema_migrations.insert().values(version=version))
            applied_now.append(version)
    return applied_now
