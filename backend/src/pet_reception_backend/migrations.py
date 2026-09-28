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


def add_m3_simulated_payment(connection: Connection) -> None:
    """Put confirmed bookings behind a bounded local payment simulation.

    Existing M3 rows remain valid confirmed bookings. New rows first reserve the
    slot as ``awaiting_payment`` and receive a public confirmation code only
    after the local simulator records a successful payment event.
    """
    connection.execute(
        text(
            "ALTER TABLE bookings "
            "ADD COLUMN IF NOT EXISTS customer_reference VARCHAR(32), "
            "ADD COLUMN IF NOT EXISTS payment_expires_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS payment_expired_at TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS payment_event_id VARCHAR(128), "
            "ADD COLUMN IF NOT EXISTS payment_method VARCHAR(64)"
        )
    )
    connection.execute(
        text(
            "UPDATE bookings SET "
            "customer_reference = COALESCE(customer_reference, confirmation_code), "
            "paid_at = COALESCE(paid_at, created_at), "
            "payment_method = COALESCE(payment_method, 'legacy_confirmed') "
            "WHERE customer_reference IS NULL OR paid_at IS NULL "
            "OR payment_method IS NULL"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE bookings "
            "ALTER COLUMN customer_reference SET NOT NULL, "
            "ALTER COLUMN confirmation_code DROP NOT NULL, "
            "ALTER COLUMN status SET DEFAULT 'awaiting_payment'"
        )
    )
    connection.execute(text("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS ck_bookings_status"))
    connection.execute(
        text(
            "ALTER TABLE bookings ADD CONSTRAINT ck_bookings_status CHECK ("
            "(status = 'awaiting_payment' AND payment_expires_at IS NOT NULL "
            "AND confirmation_code IS NULL AND paid_at IS NULL "
            "AND payment_expired_at IS NULL) OR "
            "(status = 'confirmed' AND confirmation_code IS NOT NULL "
            "AND paid_at IS NOT NULL AND payment_expired_at IS NULL) OR "
            "(status = 'payment_expired' AND confirmation_code IS NULL "
            "AND paid_at IS NULL AND payment_expired_at IS NOT NULL))"
        )
    )
    for constraint_name, columns in (
        ("uq_bookings_customer_reference", "customer_reference"),
        ("uq_bookings_payment_event", "merchant_id, payment_event_id"),
    ):
        connection.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                f"WHERE conname = '{constraint_name}') THEN "
                f"ALTER TABLE bookings ADD CONSTRAINT {constraint_name} "
                f"UNIQUE ({columns}); END IF; END $$"
            )
        )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_bookings_active_payment_hold "
            "ON bookings (merchant_id, staff_id, payment_expires_at) "
            "WHERE status = 'awaiting_payment'"
        )
    )


def add_m4_grooming_catalog(connection: Connection) -> None:
    """Persist bounded grooming facts used for service explanation/recommendation."""
    connection.execute(
        text(
            "ALTER TABLE merchants "
            "ADD COLUMN IF NOT EXISTS grooming_policy JSONB"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE merchant_services "
            "ADD COLUMN IF NOT EXISTS supported_species JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "ADD COLUMN IF NOT EXISTS included_items JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "ADD COLUMN IF NOT EXISTS recommended_for JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "ADD COLUMN IF NOT EXISTS assessment_required_for JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "ADD COLUMN IF NOT EXISTS not_included JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "ADD COLUMN IF NOT EXISTS source VARCHAR(200)"
        )
    )


def add_m4_session_and_age_contract(connection: Connection) -> None:
    """Preserve exact month-age wording without rewriting legacy approximate ages."""
    connection.execute(
        text(
            "ALTER TABLE booking_drafts "
            "ADD COLUMN IF NOT EXISTS pet_age_months INTEGER, "
            "ADD COLUMN IF NOT EXISTS pet_age_text VARCHAR(80)"
        )
    )


def add_m4_public_merchant_profile(connection: Connection) -> None:
    """Expose public hours and bounded staff facts separately from scheduling internals."""
    connection.execute(
        text(
            "ALTER TABLE merchants "
            "ADD COLUMN IF NOT EXISTS business_hours JSONB"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE staff_members "
            "ADD COLUMN IF NOT EXISTS public_intro TEXT, "
            "ADD COLUMN IF NOT EXISTS specialties JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE bookings "
            "ADD COLUMN IF NOT EXISTS pet_age_months INTEGER, "
            "ADD COLUMN IF NOT EXISTS pet_age_text VARCHAR(80)"
        )
    )
    connection.execute(
        text(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = 'ck_booking_drafts_pet_age_months') THEN "
            "ALTER TABLE booking_drafts ADD CONSTRAINT "
            "ck_booking_drafts_pet_age_months CHECK (pet_age_months IS NULL OR "
            "(pet_age_months >= 0 AND pet_age_months <= 600)); END IF; END $$"
        )
    )


def add_grooming_service_configuration(connection: Connection) -> None:
    """Store optional, strongly validated Stage A pricing/service rules."""
    connection.execute(
        text(
            "ALTER TABLE merchant_services "
            "ADD COLUMN IF NOT EXISTS service_configuration JSONB"
        )
    )
    connection.execute(
        text(
            "UPDATE merchant_services SET service_configuration = NULL "
            "WHERE service_configuration = 'null'::jsonb"
        )
    )


def add_grooming_booking_combinations(connection: Connection) -> None:
    """Persist Stage B customer selections, schedule facts and frozen deposits."""
    connection.execute(text(
        "ALTER TABLE booking_drafts "
        "ADD COLUMN IF NOT EXISTS pet_weight JSONB, "
        "ADD COLUMN IF NOT EXISTS selected_addon_ids JSONB NOT NULL DEFAULT '[]'::jsonb"
    ))
    connection.execute(text(
        "ALTER TABLE availability_searches "
        "ADD COLUMN IF NOT EXISTS scheduling_duration_minutes INTEGER, "
        "ADD COLUMN IF NOT EXISTS combination_fingerprint VARCHAR(64)"
    ))
    connection.execute(text(
        "UPDATE availability_searches s SET "
        "scheduling_duration_minutes = ms.duration_minutes, "
        "combination_fingerprint = md5(concat(s.service_id, ':legacy:', "
        "ms.duration_minutes)) || md5(concat(s.service_id, ':legacy:', "
        "ms.duration_minutes)) "
        "FROM merchant_services ms WHERE ms.merchant_id = s.merchant_id "
        "AND ms.id = s.service_id AND (s.scheduling_duration_minutes IS NULL "
        "OR s.combination_fingerprint IS NULL)"
    ))
    connection.execute(text(
        "ALTER TABLE availability_searches "
        "ALTER COLUMN scheduling_duration_minutes SET NOT NULL, "
        "ALTER COLUMN combination_fingerprint SET NOT NULL"
    ))
    connection.execute(text(
        "ALTER TABLE bookings "
        "ADD COLUMN IF NOT EXISTS pet_weight JSONB, "
        "ADD COLUMN IF NOT EXISTS selected_addon_ids JSONB NOT NULL DEFAULT '[]'::jsonb, "
        "ADD COLUMN IF NOT EXISTS deposit_amount_cents INTEGER, "
        "ADD COLUMN IF NOT EXISTS deposit_currency VARCHAR(3), "
        "ADD COLUMN IF NOT EXISTS deposit_application VARCHAR(64), "
        "ADD COLUMN IF NOT EXISTS deposit_note TEXT, "
        "ADD COLUMN IF NOT EXISTS payment_amount_cents INTEGER, "
        "ADD COLUMN IF NOT EXISTS payment_currency VARCHAR(3)"
    ))
    connection.execute(text(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE "
        "conname = 'ck_bookings_deposit_amount') THEN ALTER TABLE bookings "
        "ADD CONSTRAINT ck_bookings_deposit_amount CHECK "
        "(deposit_amount_cents IS NULL OR deposit_amount_cents > 0); END IF; END $$"
    ))
    connection.execute(text(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE "
        "conname = 'ck_bookings_payment_amount') THEN ALTER TABLE bookings "
        "ADD CONSTRAINT ck_bookings_payment_amount CHECK "
        "(payment_amount_cents IS NULL OR payment_amount_cents > 0); END IF; END $$"
    ))


MIGRATIONS: tuple[tuple[str, Callable[[Connection], None]], ...] = (
    ("0001_m2_merchant_and_booking_drafts", create_m2_schema),
    ("0002_m2_staff_and_availability", add_m2_availability_schema),
    ("0003_m2_schedule_revision", add_m2_schedule_revision),
    ("0004_m2_review_reconciliation", add_m2_review_reconciliation),
    ("0005_m3_booking", add_m3_booking_schema),
    ("0006_m3_simulated_payment", add_m3_simulated_payment),
    ("0007_m4_grooming_catalog", add_m4_grooming_catalog),
    ("0008_m4_session_and_age_contract", add_m4_session_and_age_contract),
    ("0009_m4_public_merchant_profile", add_m4_public_merchant_profile),
    ("0010_grooming_service_configuration", add_grooming_service_configuration),
    ("0011_grooming_booking_combinations", add_grooming_booking_combinations),
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
