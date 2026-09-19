from contextlib import asynccontextmanager
from datetime import datetime
from hmac import compare_digest
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

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
    BookingOperationResult,
    ConfirmBookingRequest,
    CustomerBookingResult,
    DatabaseStatus,
    DraftFieldName,
    MerchantToolResult,
    MerchantBookingList,
    MarkPreparationPresentedRequest,
    PrepareBookingRequest,
    StartBookingSessionRequest,
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
    BookingAlreadySubmittedError,
    BookingConfirmationConflictError,
    BookingCredentialError,
    BookingInvalidFieldsError,
    BookingNeedsInputError,
    BookingPaymentConflictError,
    BookingPaymentExpiredError,
    BookingPreparationExpiredError,
    BookingPreparationNotFoundError,
    BookingPreparationNotPresentedError,
    CandidateUnavailableError,
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
        store = PostgresStore(
            probe.get_engine(),
            booking_hold_minutes=resolved_settings.booking_hold_minutes,
            payment_hold_minutes=resolved_settings.payment_hold_minutes,
            booking_credential_secret=resolved_settings.booking_credential_secret,
            web_origin=resolved_settings.web_origin,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        probe.close()

    application = FastAPI(
        title="Pet Reception Backend",
        version="0.5.0",
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
            "X-Presentation-Event-ID",
            "X-Confirmation-Event-ID",
            "X-Payment-Event-ID",
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

    def trusted_admin_merchant(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_merchant_id: str = Header(min_length=1, max_length=128),
    ) -> str:
        supplied_token = credentials.credentials if credentials else ""
        if (
            not resolved_settings.merchant_admin_token
            or not supplied_token
            or not compare_digest(
                supplied_token,
                resolved_settings.merchant_admin_token,
            )
        ):
            raise HTTPException(status_code=401, detail="merchant authentication failed")
        if x_merchant_id != resolved_settings.demo_merchant_id:
            raise HTTPException(status_code=403, detail="merchant is not bound to this admin")
        return x_merchant_id

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
        if draft.selected_candidate_id is None:
            missing_for_booking.append("selected_availability")
        return BookingDraftToolResult(
            draft=draft,
            context=context,
            changed_fields=changed_fields or [],
            missing_for_availability=missing_for_availability,
            missing_for_booking=missing_for_booking,
        )

    def already_submitted_detail(error: BookingAlreadySubmittedError) -> dict:
        return {
            "code": "booking_already_submitted",
            "message": "当前会话已有预约；可以查询原预约，开始另一笔请使用页面上的开始新预约。",
            "booking_id": str(error.booking_id),
            "operation_id": str(error.operation_id),
            "booking_reference": error.customer_reference,
            "status": error.status,
            "recovery": {
                "action": "use_start_new_booking_control",
                "label": "开始新预约",
                "gateway_path": "/api/pet-reception/booking-sessions",
            },
        }

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
        merchant_now = datetime.now(ZoneInfo(merchant.time_zone))
        return MerchantToolResult(
            merchant=merchant,
            merchant_local_date=merchant_now.date(),
            merchant_local_datetime=merchant_now,
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

    @application.post(
        "/api/internal/voice/booking-sessions/start",
        response_model=BookingDraftToolResult,
        status_code=status.HTTP_201_CREATED,
    )
    def start_booking_session(
        request: StartBookingSessionRequest,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingDraftToolResult:
        try:
            draft = current_store.start_booking_session(
                context, request.previous_voice_session_id
            )
        except MerchantNotFoundError as error:
            raise HTTPException(status_code=404, detail="merchant not found") from error
        except ValueError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "booking_session_start_conflict", "message": str(error)},
            ) from error
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
        except BookingAlreadySubmittedError as error:
            raise HTTPException(
                status_code=409,
                detail=already_submitted_detail(error),
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
                detail={
                    "code": "past_date",
                    "preferred_date": error.preferred_date.isoformat(),
                    "merchant_today": error.merchant_today.isoformat(),
                },
            ) from error
        except BookingAlreadySubmittedError as error:
            raise HTTPException(
                status_code=409,
                detail=already_submitted_detail(error),
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

    def booking_error(error: Exception) -> None:
        if isinstance(error, BookingNeedsInputError):
            raise HTTPException(
                status_code=422,
                detail={"code": "needs_input", "missing_fields": list(error.missing_fields)},
            ) from error
        if isinstance(error, BookingInvalidFieldsError):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_booking_fields",
                    "invalid_fields": error.invalid_fields,
                },
            ) from error
        if isinstance(error, BookingPreparationNotFoundError):
            raise HTTPException(status_code=404, detail="booking operation not found") from error
        if isinstance(error, BookingPreparationExpiredError):
            raise HTTPException(
                status_code=409,
                detail={"code": "expired", "message": "重新查询空档并确认"},
            ) from error
        if isinstance(error, BookingPreparationNotPresentedError):
            raise HTTPException(
                status_code=409,
                detail={"code": "confirmation_not_ready"},
            ) from error
        if isinstance(error, BookingAlreadySubmittedError):
            raise HTTPException(
                status_code=409,
                detail=already_submitted_detail(error),
            ) from error
        if isinstance(error, CandidateUnavailableError):
            raise HTTPException(
                status_code=409,
                detail={"code": "unavailable", "reason": str(error)},
            ) from error
        if isinstance(error, BookingConfirmationConflictError):
            raise HTTPException(
                status_code=409,
                detail={"code": "conflict", "reason": str(error)},
            ) from error
        if isinstance(error, RuntimeError):
            raise HTTPException(status_code=503, detail="booking credential is not configured") from error
        raise error

    booking_errors = (
        BookingNeedsInputError,
        BookingInvalidFieldsError,
        BookingPreparationNotFoundError,
        BookingPreparationExpiredError,
        BookingPreparationNotPresentedError,
        BookingAlreadySubmittedError,
        CandidateUnavailableError,
        BookingConfirmationConflictError,
        RuntimeError,
    )

    @application.post(
        "/api/internal/voice/booking-preparations",
        response_model=BookingOperationResult,
    )
    def prepare_booking(
        request: PrepareBookingRequest,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingOperationResult:
        try:
            return current_store.prepare_booking(
                context,
                request.expected_version,
                request.candidate_id,
            )
        except booking_errors as error:
            booking_error(error)
            raise AssertionError("unreachable")

    @application.post(
        "/api/internal/voice/booking-preparations/{preparation_id}/presented",
        response_model=BookingOperationResult,
    )
    def mark_booking_preparation_presented(
        preparation_id: UUID,
        request: MarkPreparationPresentedRequest,
        x_presentation_event_id: str = Header(min_length=8, max_length=128),
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingOperationResult:
        try:
            return current_store.mark_booking_preparation_presented(
                context,
                preparation_id,
                request.expected_draft_version,
                x_presentation_event_id,
            )
        except booking_errors as error:
            booking_error(error)
            raise AssertionError("unreachable")

    @application.get(
        "/api/internal/voice/booking-preparations/{preparation_id}",
        response_model=BookingOperationResult,
    )
    def get_booking_preparation(
        preparation_id: UUID,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingOperationResult:
        try:
            return current_store.get_booking_preparation(context, preparation_id)
        except booking_errors as error:
            booking_error(error)
            raise AssertionError("unreachable")

    @application.post(
        "/api/internal/voice/booking-preparations/{preparation_id}/confirm",
        response_model=BookingOperationResult,
    )
    def confirm_booking(
        preparation_id: UUID,
        request: ConfirmBookingRequest,
        x_confirmation_event_id: str = Header(min_length=8, max_length=128),
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingOperationResult:
        try:
            return current_store.confirm_booking(
                context,
                preparation_id,
                request.expected_draft_version,
                x_confirmation_event_id,
            )
        except booking_errors as error:
            booking_error(error)
            raise AssertionError("unreachable")

    @application.get(
        "/api/internal/voice/booking-operations/{operation_id}",
        response_model=BookingOperationResult,
    )
    def get_booking_status(
        operation_id: UUID,
        context: VoiceRequestContext = Depends(trusted_voice_context),
        current_store: DataStore = Depends(require_store),
    ) -> BookingOperationResult:
        try:
            return current_store.get_booking_status(context, operation_id)
        except booking_errors as error:
            booking_error(error)
            raise AssertionError("unreachable")

    @application.get(
        "/api/customer/bookings/{booking_reference}",
        response_model=CustomerBookingResult,
    )
    def customer_booking(
        booking_reference: str,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        current_store: DataStore = Depends(require_store),
    ) -> CustomerBookingResult:
        token = credentials.credentials if credentials else ""
        try:
            return CustomerBookingResult(
                booking=current_store.get_customer_booking(booking_reference, token)
            )
        except BookingCredentialError as error:
            raise HTTPException(status_code=404, detail="booking not found") from error

    @application.post(
        "/api/customer/bookings/{booking_reference}/simulate-payment",
        response_model=CustomerBookingResult,
    )
    def simulate_customer_payment(
        booking_reference: str,
        x_payment_event_id: str = Header(min_length=8, max_length=128),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        current_store: DataStore = Depends(require_store),
    ) -> CustomerBookingResult:
        """Local-only payment success simulator; accepts no card data."""
        token = credentials.credentials if credentials else ""
        try:
            return CustomerBookingResult(
                booking=current_store.simulate_payment(
                    booking_reference,
                    token,
                    x_payment_event_id,
                )
            )
        except BookingCredentialError as error:
            raise HTTPException(status_code=404, detail="booking not found") from error
        except BookingPaymentExpiredError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "payment_expired", "message": "支付保留已过期，请重新预约"},
            ) from error
        except BookingPaymentConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "payment_conflict", "reason": str(error)},
            ) from error

    @application.get(
        "/api/internal/merchant/bookings",
        response_model=MerchantBookingList,
    )
    def merchant_bookings(
        merchant_id: str = Depends(trusted_admin_merchant),
        current_store: DataStore = Depends(require_store),
    ) -> MerchantBookingList:
        return current_store.list_merchant_bookings(merchant_id)

    @application.get(
        "/api/internal/merchant/bookings/{booking_id}",
        response_model=CustomerBookingResult,
    )
    def merchant_booking(
        booking_id: UUID,
        merchant_id: str = Depends(trusted_admin_merchant),
        current_store: DataStore = Depends(require_store),
    ) -> CustomerBookingResult:
        try:
            return CustomerBookingResult(
                booking=current_store.get_merchant_booking(merchant_id, booking_id)
            )
        except BookingPreparationNotFoundError as error:
            raise HTTPException(status_code=404, detail="booking not found") from error

    return application


app = create_app()
