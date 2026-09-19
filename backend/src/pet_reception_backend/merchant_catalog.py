import json
from pathlib import Path

from .schemas import DemoMerchant

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEMO_MERCHANT_PATH = PROJECT_ROOT / "config" / "demo-merchant.json"


def _load_demo_merchant() -> DemoMerchant:
    with DEMO_MERCHANT_PATH.open(encoding="utf-8") as merchant_file:
        return DemoMerchant.model_validate(json.load(merchant_file))


DEMO_MERCHANT = _load_demo_merchant()


def get_demo_merchant(merchant_id: str) -> DemoMerchant | None:
    if merchant_id != DEMO_MERCHANT.id:
        return None
    return DEMO_MERCHANT
