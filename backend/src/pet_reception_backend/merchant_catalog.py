import json
from pathlib import Path

from .schemas import DemoMerchant, DemoMerchantConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DEMO_MERCHANT_PATH = PROJECT_ROOT / "config" / "demo-merchant.json"


def resolve_merchant_config_path(config_path: str | Path | None = None) -> Path:
    path = Path(config_path) if config_path else DEFAULT_DEMO_MERCHANT_PATH
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_demo_merchant(config_path: str | Path | None = None) -> DemoMerchantConfig:
    merchant_path = resolve_merchant_config_path(config_path)
    with merchant_path.open(encoding="utf-8") as merchant_file:
        return DemoMerchantConfig.model_validate(json.load(merchant_file))


def get_demo_merchant(merchant: DemoMerchant, merchant_id: str) -> DemoMerchant | None:
    if merchant_id != merchant.id:
        return None
    return merchant
