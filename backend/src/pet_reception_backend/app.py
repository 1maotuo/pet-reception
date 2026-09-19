from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Protocol
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .database import DatabaseProbe
from .merchant_catalog import load_demo_merchant
from .schemas import (
    BookingDraft,
    BookingDraftPatch,
    BookingDraftToolResult,
    AvailabilitySearchRequest,
    AvailabilityToolResult,
    DatabaseStatus,
    DraftFieldName,
    MerchantToolResult,
    SystemStatus,
    VoiceRequestContext,
)
from .store import (
    DataStore,
    AvailabilityNeedsInputError,
    AvailabilityPastDateError,
    AvailabilityRevisionConflictError,
    AvailabilityScheduleChangedError,
    AvailabilitySearchNotFoundError,
    DraftVersionConflictError,
    MerchantNotFoundError,
    PostgresStore,
    StaffNotQualifiedError,
    UnknownServiceError,
    UnknownStaffError,
)


class Probe(Protocol):
    def check(self) -> DatabaseStatus: ...

    def close(self) -> None: ...


bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: Settings | None = None,
    database_probe: Probe | None = None,
    data_store: DataStore | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configured_merchant = load_demo_merchant(resolved_settings.merchant_config_path)
    if configured_merchant.id != resolved_settings.demo_merchant_id:
        raise ValueError(
            "DEMO_MERCHANT_ID must match the trusted merchant configuration"
        )
    probe = database_probe or DatabaseProbe(
        resolved_settings.database_url,
        resolved_settings.database_connect_timeout_seconds,
    )
    store = data_store
    if (
        store is None
        and isinstance(probe, DatabaseProbe)
        and resolved_settings.database_url
    ):
        store = PostgresStore(probe.get_engine())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        probe.close()

    application = FastAPI(
        title="Pet Reception Backend",
        version="0.3.1",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved_settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "PATCH", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Merchant-ID",
            "X-Voice-Owner-ID",
            "X-Voice-Session-ID",
            "X-Voice-Turn-ID",
        ],
    )

    def current_status() -> SystemStatus:
        database = probe.check()
        return SystemStatus(
            status="ok" if database.status == "connected" else "degraded",
            database=database,
        )

    @application.get("/health", response_model=SystemStatus)
    def health() -> SystemStatus:
        return current_status()

    @application.get("/readyz", response_model=SystemStatus)
    def readiness(response: Response) -> SystemStatus:
        result = current_status()
        if result.status != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @application.get("/api/system/status", response_model=SystemStatus)
    def system_status() -> SystemStatus:
        return current_status()

    def require_store() -> DataStore:
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="PostgreSQL business store is not configured",
            )
        return store

    def trusted_voice_context(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_voice_owner_id: str = Header(min_length=6, max_length=128),
        x_voice_session_id: str = Header(min_length=1, max_length=128),
        x_voice_turn_id: str = Header(min_length=1, max_length=128),
        x_merchant_id: str = Header(min_length=1, max_length=128),
    ) -> VoiceRequestContext:
        expected_token = resolved_settings.voice_gateway_service_token
        supplied_token = credentials.credentials if credentials else ""
        if (
            not expected_token
            or not supplied_token
            or not compare_digest(supplied_token, expected_token)
        ):
            raise HTTPException(status_code=401, detail="voice gateway authentication failed")
        if x_merchant_id != resolved_settings.demo_merchant_id:
            raise HTTPException(status_code=403, detail="merchant is not bound to this gateway")
        return VoiceRequestContext(
            merchant_id=x_merchant_id,
            owner_id=x_voice_owner_id,
            voice_session_id=x_voice_session_id,
            turn_id=x_voice_turn_id,
        )

    def draft_result(
        draft: BookingDraft,
        context: VoiceRequestContext,
        changed_fields: list[DraftFieldName] | None = None,
    ) -> BookingDraftToolResult:
        missing_for_availability = [
            field
            for field in ("service_id", "preferred_date")
            if getattr(draft, field) is None
        ]
        if draft.staff_preference_text and not draft.staff_id:
            missing_for_availability.append("staff_id")
        missing_for_booking = [
            field
            for field in (
                "customer_name",
                "contact_phone",
                "pet_name",
                "pet_species",
                "service_id",
                "preferred_date",
            )
            if getattr(draft, field) is None
        ]
        missing_for_booking.append("selected_availability")
        return BookingDraftToolResult(
            draft=draft,
            context=context,
            changed_fields=changed_fields or [],
            missing_for_availability=missing_for_availability,
            missing_for_booking=missing_for_booking,
        )

    @application.get(
        "/api/internal/voice/merchant-info",
        response_model=MerchantToolResult,
    )
    def merchant_info(
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> MerchantToolResult:
        merchant = current_store.get_merchant(context.merchant_id)
        if merchant is None:
            raise HTTPException(status_code=404, detail="merchant not found")
        return MerchantToolResult(
            merchant=merchant,
            context=context,
        )

    @application.get(
        "/api/internal/voice/booking-draft",
        response_model=BookingDraftToolResult,
    )
    def get_booking_draft(
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingDraftToolResult:
        try:
            draft = current_store.get_or_create_draft(context)
        except MerchantNotFoundError as error:
            raise HTTPException(status_code=404, detail="merchant not found") from error
        return draft_result(draft, context)

    @application.patch(
        "/api/internal/voice/booking-draft",
        response_model=BookingDraftToolResult,
    )
    def update_booking_draft(
        patch: BookingDraftPatch,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingDraftToolResult:
        try:
            draft, changed_fields = current_store.update_draft(
                context,
                patch.expected_version,
                patch.changes(),
            )
        except DraftVersionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "draft_version_conflict",
                    "current_version": error.current_version,
                },
            ) from error
        except UnknownServiceError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_service", "service_id": str(error)},
            ) from error
        except UnknownStaffError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_staff", "staff_id": str(error)},
            ) from error
        except StaffNotQualifiedError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "staff_not_qualified", "staff_id": str(error)},
            ) from error
        except MerchantNotFoundError as error:
            raise HTTPException(status_code=404, detail="merchant not found") from error
        return draft_result(draft, context, changed_fields)

    @application.post(
        "/api/internal/voice/availability-searches",
        response_model=AvailabilityToolResult,
    )
    def search_availability(
        request: AvailabilitySearchRequest,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> AvailabilityToolResult:
        try:
            result = current_store.search_availability(
                context,
                request.expected_availability_revision,
            )
        except AvailabilityRevisionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "availability_revision_conflict",
                    "current_revision": error.current_revision,
                },
            ) from error
        except AvailabilityScheduleChangedError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "merchant_schedule_changed",
                    "current_revision": error.current_revision,
                },
            ) from error
        except AvailabilityNeedsInputError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "needs_input",
                    "missing_fields": list(error.missing_fields),
                },
            ) from error
        except AvailabilityPastDateError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "past_date", "preferred_date": str(error)},
            ) from error
        except (UnknownServiceError, UnknownStaffError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "unavailable", "reference": str(error)},
            ) from error
        except MerchantNotFoundError as error:
            raise HTTPException(status_code=404, detail="merchant not found") from error
        return AvailabilityToolResult(search=result, context=context)

    @application.get(
        "/api/internal/voice/availability-searches/{search_id}",
        response_model=AvailabilityToolResult,
    )
    def get_availability_search(
        search_id: UUID,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> AvailabilityToolResult:
        try:
            result = current_store.get_availability_search(context, search_id)
        except AvailabilitySearchNotFoundError as error:
            raise HTTPException(status_code=404, detail="availability search not found") from error
        except MerchantNotFoundError as error:
            raise HTTPException(status_code=404, detail="merchant not found") from error
        return AvailabilityToolResult(search=result, context=context)

    return application


app = create_app()
