import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import logging

from fastapi import (
    APIRouter,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters.base import (
    AlertSource,
    CommandLookup,
    CommandLookupState,
    CommandResultReader,
    MonitoringControl,
    RuntimeStatusReader,
)
from .adapters.errors import (
    PresentationConflictError,
    PresentationServiceUnavailableError,
)
from .adapters.fakes import FakeAlertSource, FakeMonitoringControl
from .composition import PresentationDependencies, build_dependencies
from .models import (
    AlertDTO,
    AlertMessageDTO,
    CommandResultDTO,
    CommandSubmissionDTO,
    RuntimeStatusDTO,
    StreamConfigDTO,
)
from .settings import ApiSettings

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Optional[ApiSettings] = None,
    dependencies: Optional[PresentationDependencies] = None,
    # Backward-compatible convenience arguments for tests
    control: Optional[MonitoringControl] = None,
    command_results: Optional[CommandResultReader] = None,
    status_reader: Optional[RuntimeStatusReader] = None,
    alert_source: Optional[AlertSource] = None,
    enable_fake_generator: Optional[bool] = None,
) -> FastAPI:
    """
    App Factory tạo ứng dụng FastAPI cho Presentation / Dashboard.
    Hỗ trợ khởi tạo tự động từ ApiSettings (fake hoặc redis mode) hoặc inject PresentationDependencies.
    """
    injected_deps: Optional[PresentationDependencies] = dependencies

    if injected_deps is None and (
        control is not None
        or command_results is not None
        or status_reader is not None
        or alert_source is not None
    ):
        # Backward-compatible explicit adapter injection for tests
        if control is None:
            default_control = FakeMonitoringControl()
            control_adapter = default_control
            command_result_adapter = command_results or default_control
            status_adapter = status_reader or default_control
        else:
            control_adapter = control
            command_result_adapter = command_results or (
                control if isinstance(control, CommandResultReader) else None
            )
            status_adapter = status_reader or (
                control if isinstance(control, RuntimeStatusReader) else None
            )
            if command_result_adapter is None or status_adapter is None:
                raise ValueError(
                    "Custom control requires command_results and status_reader"
                )
        alert_adapter = alert_source or FakeAlertSource()
        fake_gen = enable_fake_generator if enable_fake_generator is not None else False
        injected_deps = PresentationDependencies(
            control=control_adapter,
            command_results=command_result_adapter,
            status_reader=status_adapter,
            alert_source=alert_adapter,
            redis_client=None,
            mode="fake",
            enable_fake_generator=fake_gen,
        )

    effective_settings = settings
    if injected_deps is None and effective_settings is None:
        effective_settings = ApiSettings.from_env()
    if enable_fake_generator is not None and injected_deps is None:
        assert effective_settings is not None
        effective_settings = ApiSettings(
            mode=effective_settings.mode,
            redis_url=effective_settings.redis_url,
            redis_prefix=effective_settings.redis_prefix,
            alert_history_scan_limit=effective_settings.alert_history_scan_limit,
            websocket_redis_block_ms=effective_settings.websocket_redis_block_ms,
            enable_fake_generator=enable_fake_generator,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        if injected_deps is not None:
            deps = injected_deps
        else:
            assert effective_settings is not None
            deps = await build_dependencies(effective_settings)

        app.state.dependencies = deps
        app.state.control = deps.control
        app.state.command_results = deps.command_results
        app.state.status_reader = deps.status_reader
        app.state.alert_source = deps.alert_source
        app.state.enable_fake_generator = deps.enable_fake_generator

        logger.info("Live Monitoring API started in '%s' mode", deps.mode)
        try:
            yield
        finally:
            logger.info("Shutting down Live Monitoring API...")
            await deps.close()
            logger.info("Live Monitoring API shutdown complete")

    app = FastAPI(
        title="Live Monitoring API",
        description="API điều khiển và giám sát luồng live HLS",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Đăng ký Exception Handlers cho Adapter Errors
    @app.exception_handler(PresentationConflictError)
    async def presentation_conflict_handler(
        request: Request, exc: PresentationConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(PresentationServiceUnavailableError)
    async def presentation_service_unavailable_handler(
        request: Request, exc: PresentationServiceUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Monitoring service is temporarily unavailable"},
        )

    # Health & Readiness Check Endpoints
    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        """Liveness probe: kiểm tra process event loop còn hoạt động."""
        return {"status": "alive"}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        """Readiness probe: kiểm tra các dependency cần thiết (Redis trong redis mode)."""
        deps: Optional[PresentationDependencies] = getattr(app.state, "dependencies", None)
        if deps is None:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "detail": "Dependencies not initialized"},
            )

        if deps.mode == "fake":
            return JSONResponse(status_code=200, content={"status": "ready", "mode": "fake"})

        # Redis mode: Ping Redis
        if deps.redis_client is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "mode": "redis",
                    "dependencies": {"redis": "unavailable"},
                },
            )

        try:
            await asyncio.wait_for(deps.redis_client.ping(), timeout=1.0)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "mode": "redis",
                    "dependencies": {"redis": "available"},
                },
            )
        except Exception as exc:
            logger.warning("Redis readiness probe failed: %s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "mode": "redis",
                    "dependencies": {"redis": "unavailable"},
                },
            )

    @app.middleware("http")
    async def add_no_cache_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Trang chủ Dashboard
    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard() -> str:
        index_file = static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="Dashboard index.html not found")
        return index_file.read_text(encoding="utf-8")

    api_router = APIRouter()

    @api_router.post(
        "/streams/{stream_id}/start",
        response_model=CommandSubmissionDTO,
        status_code=202,
    )
    async def start_stream(
        stream_id: str,
        config: StreamConfigDTO,
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", min_length=1, max_length=256
        ),
    ) -> CommandSubmissionDTO:
        if stream_id != config.stream_id:
            raise HTTPException(
                status_code=400,
                detail="Mã stream_id trên URL và trong body không khớp nhau",
            )
        ctrl: MonitoringControl = app.state.control
        alt: AlertSource = app.state.alert_source

        submission = await ctrl.start_stream(config, idempotency_key=idempotency_key)

        # Nếu đang ở fake mode và bật fake generator, khởi động loop sinh alert giả lập
        if app.state.enable_fake_generator and isinstance(alt, FakeAlertSource):
            alt.start_generating_fake_alerts(stream_id)

        return submission

    @api_router.post(
        "/streams/{stream_id}/pause",
        response_model=CommandSubmissionDTO,
        status_code=202,
    )
    async def pause_stream(
        stream_id: str,
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", min_length=1, max_length=256
        ),
    ) -> CommandSubmissionDTO:
        ctrl: MonitoringControl = app.state.control
        submission = await ctrl.pause_stream(stream_id, idempotency_key=idempotency_key)
        alt: AlertSource = app.state.alert_source
        reader: RuntimeStatusReader = app.state.status_reader
        status = await reader.get_status(stream_id)
        if (
            app.state.enable_fake_generator
            and isinstance(alt, FakeAlertSource)
            and status is not None
            and status.status.value == "PAUSED"
        ):
            await alt.stop_stream(stream_id)
        return submission

    @api_router.post(
        "/streams/{stream_id}/resume",
        response_model=CommandSubmissionDTO,
        status_code=202,
    )
    async def resume_stream(
        stream_id: str,
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", min_length=1, max_length=256
        ),
    ) -> CommandSubmissionDTO:
        ctrl: MonitoringControl = app.state.control
        submission = await ctrl.resume_stream(stream_id, idempotency_key=idempotency_key)
        alt: AlertSource = app.state.alert_source
        reader: RuntimeStatusReader = app.state.status_reader
        status = await reader.get_status(stream_id)
        if (
            app.state.enable_fake_generator
            and isinstance(alt, FakeAlertSource)
            and status is not None
            and status.status.value == "RUNNING"
        ):
            alt.start_generating_fake_alerts(stream_id)
        return submission

    @api_router.post(
        "/streams/{stream_id}/stop",
        response_model=CommandSubmissionDTO,
        status_code=202,
    )
    async def stop_stream(
        stream_id: str,
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", min_length=1, max_length=256
        ),
    ) -> CommandSubmissionDTO:
        ctrl: MonitoringControl = app.state.control
        alt: AlertSource = app.state.alert_source

        submission = await ctrl.stop_stream(stream_id, idempotency_key=idempotency_key)
        if isinstance(alt, FakeAlertSource):
            await alt.stop_stream(stream_id)
        return submission

    @api_router.put(
        "/streams/{stream_id}/config",
        response_model=CommandSubmissionDTO,
        status_code=202,
    )
    async def update_stream_config(
        stream_id: str,
        config: StreamConfigDTO,
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", min_length=1, max_length=256
        ),
    ) -> CommandSubmissionDTO:
        if stream_id != config.stream_id:
            raise HTTPException(
                status_code=400,
                detail="Mã stream_id trên URL và trong body không khớp nhau",
            )
        ctrl: MonitoringControl = app.state.control
        return await ctrl.update_config(config, idempotency_key=idempotency_key)

    @api_router.get(
        "/streams/{stream_id}/status",
        response_model=RuntimeStatusDTO,
    )
    async def get_stream_status(stream_id: str) -> RuntimeStatusDTO:
        reader: RuntimeStatusReader = app.state.status_reader
        status = await reader.get_status(stream_id)
        if not status:
            raise HTTPException(status_code=404, detail="Không tìm thấy stream này")
        return status

    @api_router.get(
        "/streams/{stream_id}/events",
        response_model=List[AlertDTO],
    )
    async def get_stream_events(
        stream_id: str,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> List[AlertDTO]:
        alt: AlertSource = app.state.alert_source
        return await alt.recent(stream_id, limit=limit)

    @api_router.get(
        "/commands/{command_id}",
        response_model=CommandResultDTO | CommandSubmissionDTO,
    )
    async def get_command_result(
        command_id: str,
        response: Response,
    ) -> CommandResultDTO | CommandSubmissionDTO:
        reader: CommandResultReader = app.state.command_results
        lookup: CommandLookup = await reader.get_command_result(command_id)
        if lookup.state == CommandLookupState.MISSING:
            raise HTTPException(status_code=404, detail="Không tìm thấy command_id này")
        if lookup.state == CommandLookupState.PENDING and lookup.submission is not None:
            response.status_code = 202
            return lookup.submission
        if lookup.state == CommandLookupState.FINAL and lookup.result is not None:
            response.status_code = 200
            return lookup.result
        raise HTTPException(status_code=500, detail="Trạng thái command không hợp lệ")

    # Public REST endpoints stay behind an explicit version boundary.
    app.include_router(api_router, prefix="/api/v1")

    @app.websocket("/api/v1/ws/streams/{stream_id}")
    async def websocket_endpoint(websocket: WebSocket, stream_id: str) -> None:
        await websocket.accept()
        alt: AlertSource = app.state.alert_source
        try:
            async for alert in alt.subscribe(stream_id):
                message = AlertMessageDTO(
                    stream_id=alert.stream_id,
                    payload=alert,
                )
                await websocket.send_json(message.model_dump(mode="json"))
        except WebSocketDisconnect:
            logger.info("Client ngắt kết nối WebSocket luồng %s", stream_id)
        except PresentationServiceUnavailableError as exc:
            logger.error("Infrastructure failure in WebSocket for stream %s: %s", stream_id, exc)
            try:
                await websocket.close(code=1013)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Lỗi trong WebSocket luồng %s: %s", stream_id, exc)
            try:
                await websocket.close(code=1011)
            except Exception:
                pass

    return app


# Default app instance cho ASGI runner (uvicorn)
app = create_app()
