import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from pet_reception_backend.app import create_app
from pet_reception_backend.config import Settings
from pet_reception_backend.database import DatabaseProbe
from pet_reception_backend.manage import ensure_test_database, setup_database
from pet_reception_backend.merchant_catalog import load_demo_merchant
from pet_reception_backend.models import (
    availability_candidates,
    availability_searches,
    booking_drafts,
    booking_preparations,
    bookings,
    merchant_services,
    merchants,
)
from pet_reception_backend.schemas import DemoMerchant, VoiceRequestContext
from pet_reception_backend.store import PostgresStore


pytestmark = pytest.mark.postgres
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-grooming-merchant.json"
MERCHANT_ID = "synthetic-grooming-test-001"


@pytest.fixture(scope="module")
def environment():
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to exercise service quotes")

    base_settings = Settings()
    database_url = base_settings.resolved_test_database_url()
    ensure_test_database(database_url)
    setup_database(database_url, base_settings.merchant_config_path)
    configured = Settings(
        database_url=database_url,
        test_database_url=database_url,
        voice_gateway_service_token="quote-test-service-token",
        demo_merchant_id=MERCHANT_ID,
        merchant_config_path=str(FIXTURE),
    )
    probe = DatabaseProbe(database_url)
    store = PostgresStore(probe.get_engine())
    store.upsert_merchant(load_demo_merchant(FIXTURE))
    store.upsert_merchant(
        DemoMerchant.model_validate(
            {
                "id": "synthetic-foreign-merchant",
                "display_name": "Foreign synthetic merchant",
                "merchant_type": "grooming",
                "time_zone": "UTC",
                "phone": "000",
                "address": "test only",
                "services": [
                    {
                        "id": "foreign-service",
                        "name_zh": "外部测试服务",
                        "duration_minutes": 30,
                        "price_note": "synthetic",
                        "supported_species": ["犬"],
                        "service_configuration": {
                            "service_kind": "package",
                            "currency": "USD",
                            "addons": [
                                {
                                    "id": "foreign-addon",
                                    "display_name": "其他商家附加项",
                                    "price": {
                                        "semantics": "fixed",
                                        "amount_cents": 1000,
                                    },
                                    "duration_minutes": 10,
                                    "source": "synthetic-test:foreign-addon",
                                }
                            ],
                            "base_price": {
                                "semantics": "fixed",
                                "amount_cents": 2000,
                            },
                            "source": "synthetic-test:foreign-service",
                        },
                    }
                ],
                "source": "synthetic-test:foreign-merchant",
            }
        )
    )
    with TestClient(create_app(configured, probe, store)) as client:
        yield client, store


def headers(owner_id: str | None = None, session_id: str | None = None) -> dict[str, str]:
    suffix = uuid4().hex
    return {
        "Authorization": "Bearer quote-test-service-token",
        "X-Merchant-ID": MERCHANT_ID,
        "X-Voice-Owner-ID": owner_id or f"owner_{suffix}",
        "X-Voice-Session-ID": session_id or f"session_{suffix}",
        "X-Voice-Turn-ID": f"turn_{suffix}",
    }


def business_counts(store: PostgresStore) -> dict[str, int]:
    tables = {
        "drafts": booking_drafts,
        "searches": availability_searches,
        "candidates": availability_candidates,
        "preparations": booking_preparations,
        "bookings": bookings,
    }
    with store.engine.connect() as connection:
        return {
            name: connection.execute(
                select(func.count()).select_from(table).where(
                    table.c.merchant_id == MERCHANT_ID
                )
            ).scalar_one()
            for name, table in tables.items()
        }


def test_configuration_round_trips_and_repeated_seed_is_idempotent(environment) -> None:
    client, store = environment
    with store.engine.connect() as connection:
        before = connection.execute(
            select(
                merchants.c.schedule_revision,
                merchants.c.updated_at,
                merchant_services.c.service_configuration,
                merchant_services.c.updated_at,
            )
            .join(
                merchant_services,
                merchant_services.c.merchant_id == merchants.c.id,
            )
            .where(
                merchants.c.id == MERCHANT_ID,
                merchant_services.c.id == "synthetic-dog-bath",
            )
        ).mappings().one()

    store.upsert_merchant(load_demo_merchant(FIXTURE))
    loaded = store.get_merchant(MERCHANT_ID)

    with store.engine.connect() as connection:
        after = connection.execute(
            select(
                merchants.c.schedule_revision,
                merchants.c.updated_at,
                merchant_services.c.service_configuration,
                merchant_services.c.updated_at,
            )
            .join(
                merchant_services,
                merchant_services.c.merchant_id == merchants.c.id,
            )
            .where(
                merchants.c.id == MERCHANT_ID,
                merchant_services.c.id == "synthetic-dog-bath",
            )
        ).mappings().one()

    assert loaded is not None
    bath = next(item for item in loaded.services if item.id == "synthetic-dog-bath")
    assert bath.service_configuration is not None
    assert bath.service_configuration.weight_tiers[1].price.amount_cents == 6000
    assert bath.service_configuration.addons[0].id == "teeth-brushing"
    assert after == before
    public_merchant = client.get(
        "/api/internal/voice/merchant-info",
        headers=headers(),
    ).json()["merchant"]
    assert all(
        "service_configuration" not in service
        for service in public_merchant["services"]
    )
    public_bath = next(
        service for service in public_merchant["services"]
        if service["id"] == "synthetic-dog-bath"
    )
    assert public_bath["combination_options"]["addons"][0] == {
        "id": "teeth-brushing",
        "display_name": "刷牙",
        "enabled": True,
        "supported_species": ["犬"],
    }
    assert "price" not in public_bath["combination_options"]["addons"][0]


def test_quote_without_draft_has_no_booking_side_effects(environment) -> None:
    client, store = environment
    trusted_headers = headers()
    before = business_counts(store)

    response = client.post(
        "/api/internal/voice/service-quotes",
        headers=trusted_headers,
        json={
            "service_id": "synthetic-dog-bath",
            "pet_species": "犬",
            "weight": {"value": "20", "unit": "lb"},
            "selected_addon_ids": ["nail-trim", "teeth-brushing"],
        },
    )

    assert response.status_code == 200
    quote = response.json()["quote"]
    assert quote["total_min_cents"] == quote["total_max_cents"] == 7200
    assert quote["scheduling_duration_minutes"] == 70
    assert quote["deposit_amount_cents"] == 2000
    assert quote["deposit_currency"] == "USD"
    assert business_counts(store) == before


def test_quote_does_not_merge_or_modify_an_existing_draft(environment) -> None:
    client, store = environment
    owner_id = f"owner_{uuid4().hex}"
    session_id = f"session_{uuid4().hex}"
    trusted_headers = headers(owner_id, session_id)
    context = VoiceRequestContext(
        merchant_id=MERCHANT_ID,
        owner_id=owner_id,
        voice_session_id=session_id,
        turn_id=trusted_headers["X-Voice-Turn-ID"],
    )
    draft = store.get_or_create_draft(context)
    draft, _ = store.update_draft(
        context,
        draft.version,
        {"service_id": "synthetic-dog-haircut"},
    )
    before_counts = business_counts(store)

    response = client.post(
        "/api/internal/voice/service-quotes",
        headers=trusted_headers,
        json={
            "service_id": "synthetic-cat-groom",
            "pet_species": "猫",
        },
    )
    after = store.get_or_create_draft(context)

    assert response.status_code == 200
    assert response.json()["quote"]["service_id"] == "synthetic-cat-groom"
    assert after.model_dump() == draft.model_dump()
    assert after.service_id == "synthetic-dog-haircut"
    assert business_counts(store) == before_counts


def test_quote_endpoint_is_protected_by_the_existing_gateway_identity(environment) -> None:
    client, _ = environment
    response = client.post(
        "/api/internal/voice/service-quotes",
        headers={
            key: value
            for key, value in headers().items()
            if key != "Authorization"
        },
        json={"service_id": "synthetic-nail-trim", "pet_species": "猫"},
    )

    assert response.status_code == 401


def test_cross_merchant_addon_is_rejected_by_the_trusted_catalog(environment) -> None:
    client, _ = environment
    response = client.post(
        "/api/internal/voice/service-quotes",
        headers=headers(),
        json={
            "service_id": "synthetic-dog-bath",
            "pet_species": "犬",
            "weight": {"value": "10", "unit": "lb"},
            "selected_addon_ids": ["foreign-addon"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "cross_merchant_addon",
        "reference": "foreign-addon",
    }
