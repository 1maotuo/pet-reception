from collections.abc import Callable

from sqlalchemy import Column, DateTime, MetaData, String, Table, func, select, text
from sqlalchemy.engine import Connection, Engine

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

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS staff_members (
                merchant_id VARCHAR(128) NOT NULL,
                id VARCHAR(128) NOT NULL,
                display_name VARCHAR(200) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                source VARCHAR(200) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (merchant_id, id),
                CONSTRAINT fk_staff_members_merchant
                    FOREIGN KEY (merchant_id) REFERENCES merchants (id)
                    ON DELETE CASCADE
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS staff_service_qualifications (
                merchant_id VARCHAR(128) NOT NULL,
                staff_id VARCHAR(128) NOT NULL,
                service_id VARCHAR(128) NOT NULL,
                PRIMARY KEY (merchant_id, staff_id, service_id),
                CONSTRAINT fk_staff_qualifications_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_staff_qualifications_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE CASCADE
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS staff_working_hours (
                id UUID PRIMARY KEY,
                merchant_id VARCHAR(128) NOT NULL,
                staff_id VARCHAR(128) NOT NULL,
                weekday INTEGER NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                CONSTRAINT fk_staff_hours_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_staff_working_window
                    UNIQUE (merchant_id, staff_id, weekday, start_time, end_time),
                CONSTRAINT ck_staff_hours_weekday
                    CHECK (weekday >= 0 AND weekday <= 6),
                CONSTRAINT ck_staff_hours_forward CHECK (start_time < end_time)
            )
            """
        )
    )

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

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS availability_searches (
                id UUID PRIMARY KEY,
                draft_id UUID NOT NULL,
                merchant_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(128) NOT NULL,
                voice_session_id VARCHAR(128) NOT NULL,
                draft_version INTEGER NOT NULL,
                availability_revision INTEGER NOT NULL,
                service_id VARCHAR(128) NOT NULL,
                preferred_date DATE NOT NULL,
                preferred_time_of_day VARCHAR(32),
                staff_id VARCHAR(128),
                merchant_time_zone VARCHAR(80) NOT NULL,
                status VARCHAR(32) NOT NULL,
                alternative_dates JSONB NOT NULL DEFAULT '[]'::jsonb,
                source VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT fk_availability_searches_draft
                    FOREIGN KEY (draft_id) REFERENCES booking_drafts (id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_availability_searches_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_availability_searches_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_availability_searches_status
                    CHECK (status IN ('available', 'no_availability'))
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS availability_candidates (
                id UUID PRIMARY KEY,
                search_id UUID NOT NULL,
                merchant_id VARCHAR(128) NOT NULL,
                staff_id VARCHAR(128) NOT NULL,
                service_id VARCHAR(128) NOT NULL,
                staff_display_name VARCHAR(200) NOT NULL,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                busy_starts_at TIMESTAMPTZ NOT NULL,
                busy_ends_at TIMESTAMPTZ NOT NULL,
                merchant_time_zone VARCHAR(80) NOT NULL,
                display_order INTEGER NOT NULL,
                CONSTRAINT fk_availability_candidates_search
                    FOREIGN KEY (search_id) REFERENCES availability_searches (id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_availability_candidates_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_availability_candidates_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT uq_candidate_search_order
                    UNIQUE (search_id, display_order),
                CONSTRAINT ck_candidate_service_interval CHECK (starts_at < ends_at),
                CONSTRAINT ck_candidate_busy_interval
                    CHECK (busy_starts_at < busy_ends_at)
            )
            """
        )
    )


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


def add_m2_review_reconciliation(connection: Connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE merchant_services "
            "ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )


def add_m3_booking_schema(connection: Connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE booking_drafts "
            "ADD COLUMN IF NOT EXISTS selected_candidate_id UUID, "
            "ADD COLUMN IF NOT EXISTS pending_preparation_id UUID, "
            "ADD COLUMN IF NOT EXISTS booking_id UUID"
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS booking_preparations (
                id UUID PRIMARY KEY,
                operation_id UUID NOT NULL UNIQUE,
                draft_id UUID NOT NULL,
                merchant_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(128) NOT NULL,
                voice_session_id VARCHAR(128) NOT NULL,
                candidate_id UUID NOT NULL,
                staff_id VARCHAR(128) NOT NULL,
                service_id VARCHAR(128) NOT NULL,
                draft_version INTEGER NOT NULL,
                availability_revision INTEGER NOT NULL,
                schedule_revision INTEGER NOT NULL,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                busy_starts_at TIMESTAMPTZ NOT NULL,
                busy_ends_at TIMESTAMPTZ NOT NULL,
                merchant_time_zone VARCHAR(80) NOT NULL,
                summary JSONB NOT NULL,
                summary_digest VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                prepared_turn_id VARCHAR(128) NOT NULL,
                presentation_id VARCHAR(128),
                presented_at TIMESTAMPTZ,
                confirmation_event_id VARCHAR(128),
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT fk_booking_preparations_draft
                    FOREIGN KEY (draft_id) REFERENCES booking_drafts (id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_booking_preparations_candidate
                    FOREIGN KEY (candidate_id) REFERENCES availability_candidates (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_booking_preparations_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_booking_preparations_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT uq_booking_preparations_confirmation_event
                    UNIQUE (merchant_id, confirmation_event_id),
                CONSTRAINT ck_booking_preparations_status CHECK (
                    status IN ('prepared', 'presented', 'confirmed', 'expired', 'cancelled')
                )
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id UUID PRIMARY KEY,
                preparation_id UUID NOT NULL UNIQUE,
                operation_id UUID NOT NULL UNIQUE,
                draft_id UUID NOT NULL,
                merchant_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(128) NOT NULL,
                voice_session_id VARCHAR(128) NOT NULL,
                candidate_id UUID NOT NULL,
                staff_id VARCHAR(128) NOT NULL,
                service_id VARCHAR(128) NOT NULL,
                customer_name VARCHAR(200) NOT NULL,
                contact_phone VARCHAR(40) NOT NULL,
                pet_name VARCHAR(200) NOT NULL,
                pet_species VARCHAR(80) NOT NULL,
                pet_breed VARCHAR(200),
                pet_age_years NUMERIC(4, 1),
                pet_care_notes TEXT,
                need_description TEXT,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                busy_starts_at TIMESTAMPTZ NOT NULL,
                busy_ends_at TIMESTAMPTZ NOT NULL,
                merchant_time_zone VARCHAR(80) NOT NULL,
                summary JSONB NOT NULL,
                summary_digest VARCHAR(64) NOT NULL,
                confirmation_event_id VARCHAR(128) NOT NULL,
                confirmation_code VARCHAR(32) NOT NULL UNIQUE,
                customer_access_token_hash VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT fk_bookings_preparation
                    FOREIGN KEY (preparation_id) REFERENCES booking_preparations (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_bookings_draft
                    FOREIGN KEY (draft_id) REFERENCES booking_drafts (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_bookings_candidate
                    FOREIGN KEY (candidate_id) REFERENCES availability_candidates (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_bookings_staff
                    FOREIGN KEY (merchant_id, staff_id)
                    REFERENCES staff_members (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_bookings_service
                    FOREIGN KEY (merchant_id, service_id)
                    REFERENCES merchant_services (merchant_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_bookings_status CHECK (status = 'confirmed')
            )
            """
        )
    )
    for constraint_name, statement in (
        (
            "fk_booking_drafts_selected_candidate",
            "ALTER TABLE booking_drafts ADD CONSTRAINT "
            "fk_booking_drafts_selected_candidate FOREIGN KEY (selected_candidate_id) "
            "REFERENCES availability_candidates (id) ON DELETE SET NULL",
        ),
        (
            "fk_booking_drafts_pending_preparation",
            "ALTER TABLE booking_drafts ADD CONSTRAINT "
            "fk_booking_drafts_pending_preparation FOREIGN KEY (pending_preparation_id) "
            "REFERENCES booking_preparations (id) ON DELETE SET NULL",
        ),
        (
            "fk_booking_drafts_booking",
            "ALTER TABLE booking_drafts ADD CONSTRAINT fk_booking_drafts_booking "
            "FOREIGN KEY (booking_id) REFERENCES bookings (id) ON DELETE RESTRICT",
        ),
    ):
        connection.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                f"WHERE conname = '{constraint_name}') THEN "
                f"{statement}; END IF; END $$"
            )
        )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_booking_preparations_active_staff "
            "ON booking_preparations (merchant_id, staff_id, expires_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_bookings_merchant_created "
            "ON bookings (merchant_id, created_at DESC)"
        )
    )


MIGRATIONS: tuple[tuple[str, Callable[[Connection], None]], ...] = (
    ("0001_m2_merchant_and_booking_drafts", create_m2_schema),
    ("0002_m2_staff_and_availability", add_m2_availability_schema),
    ("0003_m2_schedule_revision", add_m2_schedule_revision),
    ("0004_m2_review_reconciliation", add_m2_review_reconciliation),
    ("0005_m3_booking", add_m3_booking_schema),
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
