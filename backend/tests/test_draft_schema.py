from pydantic import ValidationError
import pytest

from pet_reception_backend.schemas import AvailabilitySearchRequest, BookingDraftPatch


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_version": 0},
        {"expected_version": 0, "pet_name": None},
        {"expected_version": 0, "pet_name": "   "},
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
