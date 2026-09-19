from dataclasses import dataclass
from decimal import Decimal
import re


_PHONE_PATTERN = re.compile(r"^[+\d\s().-]+$")
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass(frozen=True, slots=True)
class NormalizedPetAge:
    display_text: str
    months: int | None
    years: Decimal | None


def normalize_contact_phone(value: str) -> str:
    normalized = value.strip()
    compact = re.sub(r"[\s().-]", "", normalized).casefold()
    forbidden = {
        "incoming_phone",
        "incomingphone",
        "当前电话",
        "来电号码",
        "本机号码",
        "现在这个",
        "unknown",
        "未知",
    }
    if compact in forbidden or not _PHONE_PATTERN.fullmatch(normalized):
        raise ValueError("contact_phone must be an actual user-provided phone number")
    if normalized.count("+") > 1 or ("+" in normalized and not normalized.startswith("+")):
        raise ValueError("contact_phone contains an invalid plus sign")
    digits = "".join(character for character in normalized if character.isdigit())
    if not 7 <= len(digits) <= 15:
        raise ValueError("contact_phone must contain between 7 and 15 digits")
    return normalized


def validate_pet_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.casefold() in {
        "未知",
        "不详",
        "不知道",
        "未提供",
        "未命名",
        "暂无",
        "unknown",
        "unnamed",
        "n/a",
    }:
        raise ValueError("pet_name must be explicitly provided by the user")
    return normalized


def normalize_pet_species(value: str) -> str:
    normalized = value.strip()
    aliases = {
        "狗": "犬",
        "狗狗": "犬",
        "犬": "犬",
        "犬类": "犬",
        "dog": "犬",
        "canine": "犬",
        "猫": "猫",
        "猫咪": "猫",
        "猫猫": "猫",
        "猫类": "猫",
        "cat": "猫",
        "feline": "猫",
    }
    return aliases.get(normalized.casefold(), normalized)


def _parse_chinese_integer(value: str) -> int:
    if not value:
        raise ValueError("missing age number")
    if all(character in _CHINESE_DIGITS for character in value):
        return int("".join(str(_CHINESE_DIGITS[character]) for character in value))
    total = 0
    current = 0
    for character in value:
        if character in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
        else:
            raise ValueError("unsupported Chinese age number")
    return total + current


def _integer(value: str) -> int:
    return int(value) if value.isascii() and value.isdigit() else _parse_chinese_integer(value)


def parse_pet_age_expression(value: str) -> NormalizedPetAge:
    display = value.strip()
    compact = re.sub(r"\s+", "", display)
    month_match = re.fullmatch(
        r"([0-9]{1,3}|[零〇一二两三四五六七八九十百]+)(?:个)?月(?:龄)?(?:大)?",
        compact,
    )
    if month_match:
        months = _integer(month_match.group(1))
        if not 0 <= months <= 600:
            raise ValueError("pet age in months must be between 0 and 600")
        return NormalizedPetAge(display_text=display, months=months, years=None)

    year_match = re.fullmatch(
        r"([0-9]{1,2}(?:\.[0-9])?|[零〇一二两三四五六七八九十]+)(?:岁|年)(?:大)?",
        compact,
    )
    if year_match:
        raw = year_match.group(1)
        years = (
            Decimal(raw)
            if re.fullmatch(r"[0-9]+(?:\.[0-9])?", raw)
            else Decimal(_parse_chinese_integer(raw))
        )
        if not Decimal("0") <= years <= Decimal("50"):
            raise ValueError("pet age in years must be between 0 and 50")
        return NormalizedPetAge(display_text=display, months=None, years=years)

    raise ValueError("pet_age_text must contain a numeric age followed by months or years")


def validate_booking_fields(draft: dict, supported_species: list[str]) -> dict[str, str]:
    invalid: dict[str, str] = {}
    try:
        normalize_contact_phone(str(draft.get("contact_phone") or ""))
    except ValueError:
        invalid["contact_phone"] = "invalid_contact_phone"
    try:
        validate_pet_name(str(draft.get("pet_name") or ""))
    except ValueError:
        invalid["pet_name"] = "invalid_pet_name"

    species = normalize_pet_species(str(draft.get("pet_species") or ""))
    normalized_supported = {normalize_pet_species(item) for item in supported_species}
    if not species or species not in normalized_supported:
        invalid["pet_species"] = "unsupported_for_service"
    return invalid
