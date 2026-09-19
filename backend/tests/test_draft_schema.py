from pydantic import ValidationError
import pytest

from pet_reception_backend.schemas import BookingDraftPatch


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
