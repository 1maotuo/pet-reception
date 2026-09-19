from pydantic import ValidationError
import pytest

from pet_reception_backend.schemas import AvailabilitySearchRequest, BookingDraftPatch


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_version": 0},
        {"expected_version": 0, "pet_name": None},
        {"expected_version": 0, "pet_name": "   "},
        {"expected_version": 0, "pet_name": "未知"},
        {"expected_version": 0, "contact_phone": "incoming_phone"},
        {"expected_version": 0, "contact_phone": "当前电话"},
        {"expected_version": 0, "contact_phone": "123"},
        {"expected_version": 0, "contact_phone": "1234567abc"},
        {"expected_version": 0, "contact_phone": "incoming_phone_13800000000"},
        {
            "expected_version": 0,
            "pet_name": "皮皮",
            "clear_fields": ["pet_name"],
        },
        {
            "expected_version": 0,
            "pet_name": "皮皮",
            "merchant_id": "model-invented-merchant",
        },
    ],
)
def test_draft_patch_rejects_ambiguous_or_untrusted_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        BookingDraftPatch.model_validate(payload)


def test_draft_patch_preserves_omitted_fields_and_normalizes_text() -> None:
    patch = BookingDraftPatch.model_validate(
        {
            "expected_version": 4,
            "pet_name": "  皮皮  ",
            "clear_fields": ["pet_breed"],
        }
    )

    assert patch.changes() == {"pet_name": "皮皮", "pet_breed": None}


def test_draft_patch_accepts_a_real_explicit_phone_number() -> None:
    patch = BookingDraftPatch.model_validate(
        {"expected_version": 0, "contact_phone": "+1 (415) 555-0188"}
    )

    assert patch.changes() == {"contact_phone": "+1 (415) 555-0188"}


@pytest.mark.parametrize(
    "expression,months,years",
    [
        ("四个月", 4, None),
        ("4个月大", 4, None),
        ("6岁", None, 6),
    ],
)
def test_draft_patch_preserves_numeric_age_with_its_unit(
    expression: str,
    months: int | None,
    years: int | None,
) -> None:
    patch = BookingDraftPatch.model_validate(
        {"expected_version": 0, "pet_age_text": expression}
    )

    assert patch.changes() == {
        "pet_age_text": expression,
        "pet_age_months": months,
        "pet_age_years": years,
    }


def test_setting_age_text_takes_precedence_over_clearing_an_old_age_representation() -> None:
    patch = BookingDraftPatch.model_validate(
        {
            "expected_version": 2,
            "pet_age_text": "四个月",
            "clear_fields": ["pet_age_years"],
        }
    )

    assert patch.changes() == {
        "pet_age_text": "四个月",
        "pet_age_months": 4,
        "pet_age_years": None,
    }


def test_clearing_one_age_representation_clears_the_logical_age() -> None:
    patch = BookingDraftPatch.model_validate(
        {"expected_version": 2, "clear_fields": ["pet_age_years"]}
    )

    assert patch.changes() == {
        "pet_age_text": None,
        "pet_age_months": None,
        "pet_age_years": None,
    }


def test_draft_patch_accepts_breed_word_when_user_explicitly_uses_it_as_name() -> None:
    patch = BookingDraftPatch.model_validate(
        {"expected_version": 0, "pet_name": "金毛", "pet_species": "狗狗"}
    )

    assert patch.changes() == {"pet_name": "金毛", "pet_species": "犬"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("merchant_id", "model-merchant"),
        ("owner_id", "model-owner"),
        ("service_id", "demo-bath"),
        ("preferred_date", "2026-10-30"),
        ("staff_id", "demo-staff-lisa"),
    ],
)
def test_availability_search_only_accepts_the_expected_revision(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        AvailabilitySearchRequest.model_validate(
            {"expected_availability_revision": 1, field: value}
        )
