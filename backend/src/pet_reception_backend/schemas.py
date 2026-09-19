from typing import Literal

from pydantic import BaseModel


class DatabaseStatus(BaseModel):
    status: Literal["connected", "unavailable", "not_configured"]
    detail: str


class SystemStatus(BaseModel):
    service: Literal["pet-reception-backend"] = "pet-reception-backend"
    status: Literal["ok", "degraded"]
    database: DatabaseStatus


class MerchantService(BaseModel):
    id: str
    name_zh: str
    duration_minutes: int
    price_note: str


class DemoMerchant(BaseModel):
    id: str
    display_name: str
    merchant_type: Literal["grooming", "hospital", "daycare"]
    time_zone: str
    phone: str
    address: str
    services: list[MerchantService]
    is_demo: Literal[True] = True
    source: str


class VoiceRequestContext(BaseModel):
    merchant_id: str
    owner_id: str
    voice_session_id: str
    turn_id: str
    source: Literal["trusted_gateway_headers"] = "trusted_gateway_headers"


class MerchantToolResult(BaseModel):
    merchant: DemoMerchant
    context: VoiceRequestContext
