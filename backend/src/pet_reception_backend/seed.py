from .merchant_catalog import load_demo_merchant
from .store import PostgresStore


def seed_demo_merchant(store: PostgresStore, merchant_config_path: str) -> str:
    merchant = load_demo_merchant(merchant_config_path)
    store.upsert_merchant(merchant)
    return merchant.id
