from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import booking_drafts, merchant_services, merchants
from .schemas import (
    BookingDraft,
    DRAFT_MUTABLE_FIELDS,
    DemoMerchant,
    DraftFieldName,
    MerchantService,
    VoiceRequestContext,
)


class MerchantNotFoundError(LookupError):
    pass


class UnknownServiceError(ValueError):
    pass


@dataclass(slots=True)
class DraftVersionConflictError(Exception):
    current_version: int


class DataStore(Protocol):
    def get_merchant(self, merchant_id: str) -> DemoMerchant | None: ...

    def get_or_create_draft(self, context: VoiceRequestContext) -> BookingDraft: ...

    def update_draft(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        changes: dict[DraftFieldName, object],
    ) -> tuple[BookingDraft, list[DraftFieldName]]: ...


class PostgresStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_merchant(self, merchant_id: str) -> DemoMerchant | None:
        with self.engine.connect() as connection:
            merchant_row = connection.execute(
                select(merchants).where(merchants.c.id == merchant_id)
            ).mappings().one_or_none()
            if merchant_row is None:
                return None
            service_rows = connection.execute(
                select(merchant_services)
                .where(merchant_services.c.merchant_id == merchant_id)
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
        )

    def upsert_merchant(self, merchant: DemoMerchant) -> None:
        merchant_values = {
            "id": merchant.id,
            "display_name": merchant.display_name,
            "merchant_type": merchant.merchant_type,
            "time_zone": merchant.time_zone,
            "phone": merchant.phone,
            "address": merchant.address,
            "is_demo": merchant.is_demo,
            "source": merchant.source,
        }
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(merchants)
                .values(**merchant_values)
                .on_conflict_do_update(
                    index_elements=[merchants.c.id],
                    set_={
                        **{key: value for key, value in merchant_values.items() if key != "id"},
                        "updated_at": func.now(),
                    },
                )
            )
            for display_order, service in enumerate(merchant.services):
                service_values = {
                    "merchant_id": merchant.id,
                    "id": service.id,
                    "name_zh": service.name_zh,
                    "duration_minutes": service.duration_minutes,
                    "price_note": service.price_note,
                    "display_order": display_order,
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

    def _draft_identity(self, context: VoiceRequestContext) -> dict[str, str]:
        return {
            "merchant_id": context.merchant_id,
            "owner_id": context.owner_id,
            "voice_session_id": context.voice_session_id,
        }

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
            self._ensure_draft(connection, context)
            row = self._select_draft(connection, context)
        return BookingDraft.model_validate(dict(row))

    def update_draft(
        self,
        context: VoiceRequestContext,
        expected_version: int,
        changes: dict[DraftFieldName, object],
    ) -> tuple[BookingDraft, list[DraftFieldName]]:
        with self.engine.begin() as connection:
            self._ensure_draft(connection, context)
            current = self._select_draft(connection, context, for_update=True)
            if current["version"] != expected_version:
                raise DraftVersionConflictError(current_version=current["version"])

            service_id = changes.get("service_id")
            if service_id is not None:
                service_exists = connection.execute(
                    select(merchant_services.c.id).where(
                        merchant_services.c.merchant_id == context.merchant_id,
                        merchant_services.c.id == service_id,
                    )
                ).scalar_one_or_none()
                if service_exists is None:
                    raise UnknownServiceError(str(service_id))

            changed_fields = [
                field
                for field in DRAFT_MUTABLE_FIELDS
                if field in changes and current[field] != changes[field]
            ]
            if not changed_fields:
                return BookingDraft.model_validate(dict(current)), []

            update_values = {field: changes[field] for field in changed_fields}
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
            connection.execute(
                booking_drafts.update()
                .where(booking_drafts.c.id == current["id"])
                .values(**update_values)
            )
            updated = self._select_draft(connection, context)
        return BookingDraft.model_validate(dict(updated)), changed_fields
