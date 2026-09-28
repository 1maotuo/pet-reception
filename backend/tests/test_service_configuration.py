import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from pet_reception_backend.merchant_catalog import load_demo_merchant
from pet_reception_backend.schemas import (
    DemoMerchant,
    MerchantService,
    ServiceConfiguration,
    ServiceQuoteRequest,
)
from pet_reception_backend.service_configuration import (
    ServiceQuoteRejectedError,
    calculate_service_quote,
)


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-grooming-merchant.json"


@pytest.fixture(scope="module")
def merchant():
    return load_demo_merchant(FIXTURE)


def quote(merchant, **values):
    return calculate_service_quote(merchant, ServiceQuoteRequest(**values))


def test_quote_is_deterministic_and_deduplicates_included_and_paid_items(
    merchant,
) -> None:
    request = ServiceQuoteRequest(
        service_id="synthetic-dog-bath",
        pet_species="dog",
        weight={"value": "20", "unit": "lb"},
        selected_addon_ids=[
            "nail-trim",
            "nail-trim",
            "teeth-brushing",
            "teeth-brushing",
        ],
    )

    first = calculate_service_quote(merchant, request)
    second = calculate_service_quote(merchant, request)

    assert first.model_dump() == second.model_dump()
    assert first.matched_weight_tier_id == "dog-20-to-40lb"
    assert first.total_semantics == "fixed"
    assert first.total_min_cents == 7200
    assert first.total_max_cents == 7200
    assert first.scheduling_duration_minutes == 70
    assert first.selected_addon_ids == ["nail-trim", "teeth-brushing"]
    assert first.duplicate_addon_ids == ["nail-trim", "teeth-brushing"]
    nail = next(item for item in first.line_items if item.item_id == "nail-trim")
    assert nail.kind == "included_selection"
    assert nail.amount_min_cents == nail.amount_max_cents == 0
    assert nail.duration_minutes == 0


@pytest.mark.parametrize(
    ("weight", "expected_tier", "expected_price"),
    [
        ({"value": "19.999", "unit": "lb"}, "dog-under-20lb", 5000),
        ({"value": "20", "unit": "lb"}, "dog-20-to-40lb", 6000),
        (
            {"value": "9.07184740", "unit": "kg"},
            "dog-20-to-40lb",
            6000,
        ),
        (
            {"value": "45", "unit": "lb", "estimated": True},
            "dog-40lb-and-up",
            7000,
        ),
    ],
)
def test_weight_boundaries_and_lb_kg_conversion(
    merchant,
    weight,
    expected_tier,
    expected_price,
) -> None:
    result = quote(
        merchant,
        service_id="synthetic-dog-bath",
        pet_species="犬",
        weight=weight,
    )

    assert result.matched_weight_tier_id == expected_tier
    assert result.total_min_cents == expected_price


def test_price_only_weight_does_not_block_availability(merchant) -> None:
    missing = quote(
        merchant,
        service_id="synthetic-dog-bath",
        pet_species="犬",
    )
    cross_tier_estimate = quote(
        merchant,
        service_id="synthetic-dog-bath",
        pet_species="犬",
        weight={"minimum": "19", "maximum": "21", "unit": "lb", "estimated": True},
    )

    assert missing.missing_for_quote == ["weight"]
    assert missing.missing_for_availability == []
    assert missing.scheduling_duration_minutes == 60
    assert missing.can_schedule is True
    assert cross_tier_estimate.missing_for_quote == ["weight_clarification"]
    assert cross_tier_estimate.missing_for_availability == []


def test_weight_that_changes_duration_is_required_before_availability() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    service = raw["services"][0]
    service["id"] = "synthetic-weight-duration"
    configuration = service["service_configuration"]
    configuration["weight_usage"] = "price_and_duration"
    for tier, duration in zip(configuration["weight_tiers"], (50, 60, 75)):
        tier["duration_minutes"] = duration
    raw["services"] = [service]
    merchant = DemoMerchant.model_validate(raw)

    missing = quote(
        merchant,
        service_id="synthetic-weight-duration",
        pet_species="犬",
    )
    complete = quote(
        merchant,
        service_id="synthetic-weight-duration",
        pet_species="犬",
        weight={"value": "20", "unit": "lb"},
    )

    assert missing.missing_for_quote == ["weight"]
    assert missing.missing_for_availability == ["weight"]
    assert missing.scheduling_duration_minutes is None
    assert missing.can_schedule is False
    assert complete.scheduling_duration_minutes == 60
    assert complete.missing_for_availability == []


def test_cat_service_has_its_own_non_dog_price_rule(merchant) -> None:
    result = quote(
        merchant,
        service_id="synthetic-cat-groom",
        pet_species="cat",
        weight={"value": "30", "unit": "lb"},
        selected_addon_ids=["cat-deshedding"],
    )

    assert result.matched_weight_tier_id is None
    assert result.total_semantics == "starting_at"
    assert result.total_min_cents == 8500
    assert result.total_max_cents is None
    assert result.scheduling_duration_minutes == 105
    with pytest.raises(ServiceQuoteRejectedError, match="species_not_supported"):
        quote(
            merchant,
            service_id="synthetic-dog-bath",
            pet_species="猫",
            weight={"value": "10", "unit": "lb"},
        )


def test_standalone_service_does_not_require_a_package(merchant) -> None:
    result = quote(
        merchant,
        service_id="synthetic-nail-trim",
        pet_species="猫",
    )

    assert result.service_kind == "standalone"
    assert result.total_min_cents == result.total_max_cents == 1800
    assert result.scheduling_duration_minutes == 15
    assert result.can_quote is True
    assert result.can_schedule is True


@pytest.mark.parametrize(
    ("addon_id", "foreign_ids", "expected_code"),
    [
        ("missing-addon", set(), "unknown_addon"),
        ("disabled-upgrade", set(), "addon_disabled"),
        ("cat-deshedding", set(), "addon_not_applicable"),
        ("foreign-addon", {"foreign-addon"}, "cross_merchant_addon"),
    ],
)
def test_invalid_addon_selection_is_explicitly_rejected(
    merchant,
    addon_id,
    foreign_ids,
    expected_code,
) -> None:
    request = ServiceQuoteRequest(
        service_id="synthetic-dog-bath",
        pet_species="犬",
        weight={"value": "15", "unit": "lb"},
        selected_addon_ids=[addon_id],
    )

    with pytest.raises(ServiceQuoteRejectedError) as caught:
        calculate_service_quote(
            merchant,
            request,
            foreign_addon_ids=foreign_ids,
        )

    assert caught.value.code == expected_code
    assert caught.value.reference == addon_id


def test_unknown_addon_duration_never_becomes_an_average(merchant) -> None:
    result = quote(
        merchant,
        service_id="synthetic-dog-bath",
        pet_species="犬",
        weight={"value": "15", "unit": "lb"},
        selected_addon_ids=["coat-treatment"],
    )

    assert result.total_semantics == "starting_at"
    assert result.total_min_cents == 7000
    assert result.scheduling_duration_minutes is None
    assert result.missing_for_availability == ["addon_duration:coat-treatment"]
    assert result.requires_assessment is True
    assert result.can_schedule is False


def test_legacy_service_returns_price_note_without_inventing_zero() -> None:
    merchant = DemoMerchant(
        id="legacy-test",
        display_name="Legacy",
        merchant_type="grooming",
        time_zone="UTC",
        phone="000",
        address="test",
        services=[
            MerchantService(
                id="legacy-bath",
                name_zh="旧洗护",
                duration_minutes=45,
                price_note="价格待店员确认",
            )
        ],
        source="synthetic-test:legacy",
    )

    result = quote(merchant, service_id="legacy-bath")

    assert result.total_semantics == "unconfigured"
    assert result.total_min_cents is None
    assert result.total_max_cents is None
    assert result.legacy_price_note == "价格待店员确认"
    assert result.missing_for_quote == ["service_pricing_configuration"]
    assert result.scheduling_duration_minutes == 45


def test_service_configuration_rejects_typos_and_ambiguous_tiers() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    configuration = raw["services"][0]["service_configuration"]
    configuration["curreny"] = configuration.pop("currency")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ServiceConfiguration.model_validate(configuration)

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    configuration = raw["services"][0]["service_configuration"]
    configuration["weight_tiers"][1]["minimum_inclusive"] = False
    with pytest.raises(ValidationError, match="exactly one adjacent tier"):
        ServiceConfiguration.model_validate(configuration)


def test_weight_requires_an_explicit_supported_unit() -> None:
    with pytest.raises(ValidationError, match="unit"):
        ServiceQuoteRequest.model_validate(
            {
                "service_id": "synthetic-dog-bath",
                "weight": {"value": "35"},
            }
        )
    with pytest.raises(ValidationError, match="Input should be 'lb' or 'kg'"):
        ServiceQuoteRequest.model_validate(
            {
                "service_id": "synthetic-dog-bath",
                "weight": {"value": "35", "unit": "oz"},
            }
        )

def test_misspelled_service_configuration_field_is_not_silently_dropped() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    service = raw["services"][0]
    service["service_configuraton"] = service.pop("service_configuration")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MerchantService.model_validate(service)
