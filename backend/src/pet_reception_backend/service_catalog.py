"""Trusted database adapter for the pure service-combination calculator."""

from hashlib import sha256
import json

from sqlalchemy import select

from .models import merchant_services, merchants
from .schemas import (
    DemoMerchant,
    MerchantService,
    QuoteWeightInput,
    ServiceConfiguration,
    ServiceQuoteRequest,
    ServiceQuoteResult,
)
from .service_configuration import calculate_service_quote


def _service(row) -> MerchantService:
    return MerchantService(
        id=row["id"],
        name_zh=row["name_zh"],
        duration_minutes=row["duration_minutes"],
        price_note=row["price_note"],
        supported_species=row["supported_species"] or [],
        included_items=row["included_items"] or [],
        recommended_for=row["recommended_for"] or [],
        assessment_required_for=row["assessment_required_for"] or [],
        not_included=row["not_included"] or [],
        service_configuration=row["service_configuration"],
        source=row["source"],
    )


def load_quote_merchant(connection, merchant_id: str) -> DemoMerchant | None:
    merchant = connection.execute(
        select(merchants).where(merchants.c.id == merchant_id)
    ).mappings().one_or_none()
    if merchant is None:
        return None
    rows = connection.execute(
        select(merchant_services)
        .where(
            merchant_services.c.merchant_id == merchant_id,
            merchant_services.c.active.is_(True),
        )
        .order_by(merchant_services.c.display_order, merchant_services.c.id)
    ).mappings()
    return DemoMerchant(
        id=merchant["id"],
        display_name=merchant["display_name"],
        merchant_type=merchant["merchant_type"],
        time_zone=merchant["time_zone"],
        phone=merchant["phone"],
        address=merchant["address"],
        is_demo=merchant["is_demo"],
        source=merchant["source"],
        services=[_service(row) for row in rows],
        grooming_policy=merchant["grooming_policy"],
        business_hours=merchant["business_hours"],
    )


def foreign_addon_ids(connection, merchant_id: str, requested: set[str]) -> set[str]:
    if not requested:
        return set()
    found: set[str] = set()
    configurations = connection.execute(
        select(merchant_services.c.service_configuration).where(
            merchant_services.c.merchant_id != merchant_id,
            merchant_services.c.service_configuration.is_not(None),
        )
    ).scalars()
    for raw in configurations:
        configuration = ServiceConfiguration.model_validate(raw)
        configured = {item.id for item in configuration.included_items}
        configured.update(item.id for item in configuration.addons)
        found.update(requested.intersection(configured))
    return found


def calculate_draft_quote(connection, merchant_id: str, draft) -> ServiceQuoteResult | None:
    service_id = draft.get("service_id")
    if not service_id:
        return None
    merchant = load_quote_merchant(connection, merchant_id)
    if merchant is None:
        return None
    addon_ids = list(draft.get("selected_addon_ids") or [])
    raw_weight = draft.get("pet_weight")
    request = ServiceQuoteRequest(
        service_id=service_id,
        pet_species=draft.get("pet_species"),
        weight=QuoteWeightInput.model_validate(raw_weight) if raw_weight else None,
        selected_addon_ids=addon_ids,
    )
    return calculate_service_quote(
        merchant,
        request,
        foreign_addon_ids=foreign_addon_ids(
            connection, merchant_id, set(addon_ids)
        ),
    )


def quote_fingerprint(quote: ServiceQuoteResult) -> str:
    payload = quote.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()
