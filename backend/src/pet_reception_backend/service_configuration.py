from collections.abc import Sequence, Set
from dataclasses import dataclass
from decimal import Decimal

from .business_rules import normalize_pet_species
from .schemas import (
    DemoMerchant,
    MerchantService,
    QuoteLineItem,
    QuoteWeightInput,
    ServiceQuoteRequest,
    ServiceQuoteResult,
    StructuredPrice,
    WeightTierConfiguration,
)


POUNDS_TO_KG = Decimal("0.45359237")


@dataclass(slots=True)
class ServiceQuoteRejectedError(ValueError):
    code: str
    reference: str

    def __str__(self) -> str:
        return f"{self.code}: {self.reference}"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _to_kg(value: Decimal, unit: str) -> Decimal:
    return value if unit == "kg" else value * POUNDS_TO_KG


def _tier_contains(tier: WeightTierConfiguration, weight_kg: Decimal) -> bool:
    minimum = _to_kg(tier.minimum_weight, tier.unit)
    maximum = (
        _to_kg(tier.maximum_weight, tier.unit)
        if tier.maximum_weight is not None
        else None
    )
    above_minimum = weight_kg > minimum or (
        weight_kg == minimum and tier.minimum_inclusive
    )
    below_maximum = maximum is None or weight_kg < maximum or (
        weight_kg == maximum and tier.maximum_inclusive
    )
    return above_minimum and below_maximum


def _match_weight_tier(
    tiers: Sequence[WeightTierConfiguration],
    weight: QuoteWeightInput,
) -> WeightTierConfiguration | None:
    minimum = weight.value if weight.value is not None else weight.minimum
    maximum = weight.value if weight.value is not None else weight.maximum
    if minimum is None or maximum is None:
        return None
    minimum_kg = _to_kg(minimum, weight.unit)
    maximum_kg = _to_kg(maximum, weight.unit)
    minimum_tier = next((tier for tier in tiers if _tier_contains(tier, minimum_kg)), None)
    maximum_tier = next((tier for tier in tiers if _tier_contains(tier, maximum_kg)), None)
    if minimum_tier is None or maximum_tier is None or minimum_tier.id != maximum_tier.id:
        return None
    return minimum_tier


def _line_from_price(
    *,
    item_id: str,
    display_name: str,
    kind: str,
    price: StructuredPrice | None,
    duration_minutes: int | None,
    fallback_note: str | None = None,
) -> QuoteLineItem:
    if price is None:
        return QuoteLineItem(
            item_id=item_id,
            display_name=display_name,
            kind=kind,
            pricing_semantics="unconfigured",
            duration_minutes=duration_minutes,
            note=fallback_note,
        )
    if price.semantics == "fixed":
        minimum = maximum = price.amount_cents
    elif price.semantics in {"starting_at", "range"}:
        minimum = price.amount_cents
        maximum = price.maximum_amount_cents
    else:
        minimum = maximum = None
    return QuoteLineItem(
        item_id=item_id,
        display_name=display_name,
        kind=kind,
        pricing_semantics=price.semantics,
        amount_min_cents=minimum,
        amount_max_cents=maximum,
        duration_minutes=duration_minutes,
        note=price.note,
    )


def _sum_prices(
    line_items: Sequence[QuoteLineItem],
) -> tuple[str, int | None, int | None]:
    if any(item.pricing_semantics == "unconfigured" for item in line_items):
        return "unconfigured", None, None
    if any(item.pricing_semantics == "assessment_required" for item in line_items):
        return "assessment_required", None, None
    minimum = sum(item.amount_min_cents or 0 for item in line_items)
    if any(item.amount_max_cents is None for item in line_items):
        return "starting_at", minimum, None
    maximum = sum(item.amount_max_cents or 0 for item in line_items)
    if minimum == maximum:
        return "fixed", minimum, maximum
    return "range", minimum, maximum


def _service_by_id(
    services: Sequence[MerchantService], service_id: str
) -> MerchantService | None:
    return next((service for service in services if service.id == service_id), None)


def calculate_service_quote(
    merchant: DemoMerchant,
    request: ServiceQuoteRequest,
    *,
    foreign_addon_ids: Set[str] = frozenset(),
) -> ServiceQuoteResult:
    """Calculate a quote from trusted catalog data without I/O or mutations."""

    service = _service_by_id(merchant.services, request.service_id)
    if service is None:
        raise ServiceQuoteRejectedError("unknown_service", request.service_id)

    normalized_species = (
        normalize_pet_species(request.pet_species) if request.pet_species else None
    )
    supported_species = [normalize_pet_species(item) for item in service.supported_species]
    if normalized_species and supported_species and normalized_species not in supported_species:
        raise ServiceQuoteRejectedError(
            "species_not_supported",
            normalized_species,
        )

    selected_ids: list[str] = []
    duplicates: list[str] = []
    for addon_id in request.selected_addon_ids:
        normalized_id = addon_id.strip()
        if not normalized_id:
            raise ServiceQuoteRejectedError("unknown_addon", addon_id)
        if normalized_id in selected_ids:
            _append_unique(duplicates, normalized_id)
        else:
            selected_ids.append(normalized_id)

    configuration = service.service_configuration
    if configuration is None:
        if selected_ids:
            addon_id = selected_ids[0]
            code = "cross_merchant_addon" if addon_id in foreign_addon_ids else "unknown_addon"
            raise ServiceQuoteRejectedError(code, addon_id)
        return ServiceQuoteResult(
            merchant_id=merchant.id,
            service_id=service.id,
            service_name=service.name_zh,
            service_kind=None,
            applicable_species=service.supported_species,
            requested_species=normalized_species,
            weight=request.weight,
            matched_weight_tier_id=None,
            included_items=[],
            selected_addon_ids=[],
            duplicate_addon_ids=duplicates,
            line_items=[
                _line_from_price(
                    item_id=service.id,
                    display_name=service.name_zh,
                    kind="base_service",
                    price=None,
                    duration_minutes=service.duration_minutes,
                    fallback_note=service.price_note,
                )
            ],
            currency=None,
            total_semantics="unconfigured",
            total_min_cents=None,
            total_max_cents=None,
            legacy_price_note=service.price_note,
            scheduling_duration_minutes=service.duration_minutes,
            deposit_amount_cents=None,
            deposit_currency=None,
            deposit_application=None,
            deposit_note=None,
            missing_for_quote=["service_pricing_configuration"],
            missing_for_availability=[],
            missing_for_booking=["service_pricing_configuration"],
            requires_assessment=False,
            assessment_reasons=[],
            can_quote=False,
            can_schedule=True,
            source=service.source,
        )

    missing_for_quote: list[str] = []
    missing_for_availability: list[str] = []
    assessment_reasons: list[str] = []
    if normalized_species is None:
        _append_unique(missing_for_quote, "pet_species")
        _append_unique(missing_for_availability, "pet_species")

    matched_tier: WeightTierConfiguration | None = None
    base_price = configuration.base_price
    scheduling_duration = service.duration_minutes
    if configuration.weight_tiers:
        if request.weight is None:
            _append_unique(missing_for_quote, "weight")
            if configuration.weight_usage in {"price_and_duration", "eligibility"}:
                _append_unique(missing_for_availability, "weight")
            if configuration.weight_usage == "price_and_duration":
                scheduling_duration = None
        else:
            matched_tier = _match_weight_tier(configuration.weight_tiers, request.weight)
            if matched_tier is None:
                _append_unique(missing_for_quote, "weight_clarification")
                if configuration.weight_usage in {"price_and_duration", "eligibility"}:
                    _append_unique(missing_for_availability, "weight_clarification")
                if configuration.weight_usage == "price_and_duration":
                    scheduling_duration = None
            else:
                base_price = matched_tier.price
                if configuration.weight_usage == "price_and_duration":
                    scheduling_duration = matched_tier.duration_minutes
                if not matched_tier.eligible:
                    _append_unique(
                        assessment_reasons,
                        matched_tier.assessment_reason or "该体重档需要人工评估",
                    )
                    _append_unique(missing_for_availability, "manual_assessment")

    if not configuration.weight_tiers and base_price is None:
        _append_unique(missing_for_quote, "service_price_configuration")

    line_items = [
        _line_from_price(
            item_id=service.id,
            display_name=service.name_zh,
            kind="base_service",
            price=base_price,
            duration_minutes=scheduling_duration,
            fallback_note=service.price_note,
        )
    ]
    if base_price and base_price.semantics == "assessment_required":
        _append_unique(
            assessment_reasons,
            base_price.note or "主服务价格需要人工评估",
        )
        _append_unique(missing_for_availability, "manual_assessment")

    included_by_id = {item.id: item for item in configuration.included_items}
    addons_by_id = {item.id: item for item in configuration.addons}
    other_current_item_ids: set[str] = set()
    for other_service in merchant.services:
        if other_service.id == service.id or other_service.service_configuration is None:
            continue
        other_current_item_ids.update(
            item.id for item in other_service.service_configuration.included_items
        )
        other_current_item_ids.update(
            item.id for item in other_service.service_configuration.addons
        )

    for addon_id in selected_ids:
        included = included_by_id.get(addon_id)
        if included is not None:
            line_items.append(
                QuoteLineItem(
                    item_id=addon_id,
                    display_name=included.display_name,
                    kind="included_selection",
                    pricing_semantics="fixed",
                    amount_min_cents=0,
                    amount_max_cents=0,
                    duration_minutes=0,
                    note="已包含在主服务中，不重复收费或增加时长",
                )
            )
            continue

        addon = addons_by_id.get(addon_id)
        if addon is None:
            if addon_id in other_current_item_ids:
                code = "addon_not_applicable"
            elif addon_id in foreign_addon_ids:
                code = "cross_merchant_addon"
            else:
                code = "unknown_addon"
            raise ServiceQuoteRejectedError(code, addon_id)
        if not addon.enabled:
            raise ServiceQuoteRejectedError("addon_disabled", addon_id)
        addon_species = [normalize_pet_species(item) for item in addon.supported_species]
        if normalized_species and addon_species and normalized_species not in addon_species:
            raise ServiceQuoteRejectedError("addon_not_applicable", addon_id)
        if addon.price is None:
            _append_unique(missing_for_quote, f"addon_price:{addon_id}")
        if addon.duration_minutes is None:
            _append_unique(missing_for_availability, f"addon_duration:{addon_id}")
            _append_unique(
                assessment_reasons,
                addon.assessment_reason or f"{addon.display_name}的服务时长需要人工评估",
            )
            scheduling_duration = None
        elif scheduling_duration is not None:
            scheduling_duration += addon.duration_minutes
        if addon.requires_assessment:
            _append_unique(
                assessment_reasons,
                addon.assessment_reason or f"{addon.display_name}需要人工评估",
            )
            _append_unique(missing_for_availability, "manual_assessment")
        if addon.price and addon.price.semantics == "assessment_required":
            _append_unique(
                assessment_reasons,
                addon.price.note or f"{addon.display_name}价格需要人工评估",
            )
            _append_unique(missing_for_availability, "manual_assessment")
        line_items.append(
            _line_from_price(
                item_id=addon.id,
                display_name=addon.display_name,
                kind="addon",
                price=addon.price,
                duration_minutes=addon.duration_minutes,
            )
        )

    total_semantics, total_minimum, total_maximum = _sum_prices(line_items)
    missing_for_booking = list(missing_for_quote)
    for field in missing_for_availability:
        _append_unique(missing_for_booking, field)
    requires_assessment = bool(assessment_reasons)
    can_quote = not missing_for_quote and total_semantics not in {
        "unconfigured",
        "assessment_required",
    }
    can_schedule = (
        scheduling_duration is not None
        and not missing_for_availability
        and not requires_assessment
    )
    if configuration.deposit is None:
        _append_unique(missing_for_booking, "deposit_configuration")

    return ServiceQuoteResult(
        merchant_id=merchant.id,
        service_id=service.id,
        service_name=service.name_zh,
        service_kind=configuration.service_kind,
        applicable_species=service.supported_species,
        requested_species=normalized_species,
        weight=request.weight,
        matched_weight_tier_id=matched_tier.id if matched_tier else None,
        included_items=configuration.included_items,
        selected_addon_ids=selected_ids,
        duplicate_addon_ids=duplicates,
        line_items=line_items,
        currency=configuration.currency,
        total_semantics=total_semantics,
        total_min_cents=total_minimum,
        total_max_cents=total_maximum,
        legacy_price_note=service.price_note,
        scheduling_duration_minutes=scheduling_duration,
        deposit_amount_cents=(
            configuration.deposit.amount_cents if configuration.deposit else None
        ),
        deposit_currency=configuration.currency if configuration.deposit else None,
        deposit_application=(
            configuration.deposit.application if configuration.deposit else None
        ),
        deposit_note=configuration.deposit.note if configuration.deposit else None,
        missing_for_quote=missing_for_quote,
        missing_for_availability=missing_for_availability,
        missing_for_booking=missing_for_booking,
        requires_assessment=requires_assessment,
        assessment_reasons=assessment_reasons,
        can_quote=can_quote,
        can_schedule=can_schedule,
        source=configuration.source,
    )
