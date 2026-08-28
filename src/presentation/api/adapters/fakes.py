import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional, Tuple
import uuid

from .base import (
    AlertSource,
    CommandLookup,
    CommandLookupState,
    CommandResultReader,
    MonitoringControl,
    RuntimeStatusReader,
)
from ..models import (
    AlertCategory,
    AlertDTO,
    AlertState,
    CommandResultDTO,
    CommandResultStatusEnum,
    CommandSubmissionDTO,
    CommandTypeEnum,
    EventType,
    RuntimeStatusDTO,
    StreamConfigDTO,
    StreamHealthEnum,
    StreamStatusEnum,
)

# Implementation giả (Fake) để điều khiển stream trong bộ nhớ RAM, phục vụ test MVP baseline
class FakeMonitoringControl(
    MonitoringControl,
    CommandResultReader,
    RuntimeStatusReader,
):
    def __init__(self) -> None:
        self._streams: Dict[str, StreamConfigDTO] = {}
        self._statuses: Dict[str, RuntimeStatusDTO] = {}
        self._commands: Dict[str, CommandResultDTO] = {}

    async def start_stream(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        command_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        existing = self._streams.get(config.stream_id)
        if existing is not None:
            current = self._statuses.get(config.stream_id)
            if existing != config:
                return self._record_result(
                    command_id, CommandTypeEnum.START, config.stream_id,
                    CommandResultStatusEnum.REJECTED, False, now,
                    error_code="STREAM_ALREADY_EXISTS",
                    error="Stream already exists with different configuration",
                )
            changed = current is not None and current.status != StreamStatusEnum.RUNNING
            if changed:
                self._statuses[config.stream_id] = current.model_copy(
                    update={"status": StreamStatusEnum.RUNNING, "observed_at": now}
                )
            return self._record_result(
                command_id,
                CommandTypeEnum.START,
                config.stream_id,
                CommandResultStatusEnum.APPLIED if changed else CommandResultStatusEnum.NOOP,
                changed,
                now,
            )

        self._streams[config.stream_id] = config
        self._statuses[config.stream_id] = RuntimeStatusDTO(
            schema_version="1.0",
            stream_id=config.stream_id,
            status=StreamStatusEnum.RUNNING,
            health=StreamHealthEnum.HEALTHY,
            started_at=now,
            last_poll_at=now,
            active_variant_count=3,
            queue_depth=5,
            queue_lag_seconds=0.0,
            telemetry_available=True,
            health_reasons=[],
            checks={
                "black_screen": "ENABLED" if config.checks.black_screen.enabled else "DISABLED",
                "audio_loss": "ENABLED" if config.checks.audio_loss.enabled else "DISABLED",
            },
            worker_id="fake-worker-01",
            observed_at=now,
        )

        self._commands[command_id] = CommandResultDTO(
            schema_version="1.0",
            command_id=command_id,
            command_type=CommandTypeEnum.START,
            stream_id=config.stream_id,
            status=CommandResultStatusEnum.APPLIED,
            changed=True,
            processed_at=now,
        )

        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=command_id,
            stream_id=config.stream_id,
            status="ACCEPTED",
            message=f"Start command accepted for stream {config.stream_id}",
        )

    def _record_result(
        self,
        command_id: str,
        command_type: CommandTypeEnum,
        stream_id: str,
        status: CommandResultStatusEnum,
        changed: bool,
        now: datetime,
        *,
        error_code: str | None = None,
        error: str | None = None,
    ) -> CommandSubmissionDTO:
        self._commands[command_id] = CommandResultDTO(
            command_id=command_id,
            command_type=command_type,
            stream_id=stream_id,
            status=status,
            changed=changed,
            processed_at=now,
            error_code=error_code,
            error=error,
        )
        return CommandSubmissionDTO(
            command_id=command_id,
            stream_id=stream_id,
            message=f"{command_type.value} command accepted for stream {stream_id}",
        )

    async def pause_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        command_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        if stream_id in self._statuses:
            status = self._statuses[stream_id]
            changed = status.status != StreamStatusEnum.PAUSED
            self._statuses[stream_id] = status.model_copy(
                update={"status": StreamStatusEnum.PAUSED, "observed_at": now}
            )
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.PAUSE,
                stream_id=stream_id,
                status=CommandResultStatusEnum.APPLIED if changed else CommandResultStatusEnum.NOOP,
                changed=changed,
                processed_at=now,
            )
            message = f"Pause command applied for stream {stream_id}"
        else:
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.PAUSE,
                stream_id=stream_id,
                status=CommandResultStatusEnum.REJECTED,
                changed=False,
                processed_at=now,
                error_code="STREAM_NOT_FOUND",
                error=f"Stream '{stream_id}' not found",
            )
            message = f"Pause command rejected: stream '{stream_id}' not found"

        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=command_id,
            stream_id=stream_id,
            status="ACCEPTED",
            message=message,
        )

    async def resume_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        command_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        if stream_id in self._statuses:
            status = self._statuses[stream_id]
            changed = status.status != StreamStatusEnum.RUNNING
            self._statuses[stream_id] = status.model_copy(
                update={"status": StreamStatusEnum.RUNNING, "observed_at": now}
            )
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.RESUME,
                stream_id=stream_id,
                status=CommandResultStatusEnum.APPLIED if changed else CommandResultStatusEnum.NOOP,
                changed=changed,
                processed_at=now,
            )
            message = f"Resume command applied for stream {stream_id}"
        else:
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.RESUME,
                stream_id=stream_id,
                status=CommandResultStatusEnum.REJECTED,
                changed=False,
                processed_at=now,
                error_code="STREAM_NOT_FOUND",
                error=f"Stream '{stream_id}' not found",
            )
            message = f"Resume command rejected: stream '{stream_id}' not found"

        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=command_id,
            stream_id=stream_id,
            status="ACCEPTED",
            message=message,
        )

    async def stop_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        command_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        if stream_id in self._statuses:
            self._statuses.pop(stream_id, None)
            self._streams.pop(stream_id, None)
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.STOP,
                stream_id=stream_id,
                status=CommandResultStatusEnum.APPLIED,
                changed=True,
                processed_at=now,
            )
            message = f"Stop command applied for stream {stream_id}"
        else:
            self._commands[command_id] = CommandResultDTO(
                schema_version="1.0",
                command_id=command_id,
                command_type=CommandTypeEnum.STOP,
                stream_id=stream_id,
                status=CommandResultStatusEnum.NOOP,
                changed=False,
                processed_at=now,
            )
            message = f"Stop command noop: stream '{stream_id}' already stopped"

        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=command_id,
            stream_id=stream_id,
            status="ACCEPTED",
            message=message,
        )

    async def update_config(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        command_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        existing = self._streams.get(config.stream_id)
        if existing is None:
            return self._record_result(
                command_id, CommandTypeEnum.UPDATE_CONFIG, config.stream_id,
                CommandResultStatusEnum.REJECTED, False, now,
                error_code="STREAM_NOT_FOUND",
                error=f"Stream '{config.stream_id}' not found",
            )
        changed = existing != config
        self._streams[config.stream_id] = config
        if changed and config.stream_id in self._statuses:
            status = self._statuses[config.stream_id]
            self._statuses[config.stream_id] = status.model_copy(
                update={
                    "checks": {
                        "black_screen": "ENABLED" if config.checks.black_screen.enabled else "DISABLED",
                        "audio_loss": "ENABLED" if config.checks.audio_loss.enabled else "DISABLED",
                    },
                    "observed_at": now,
                }
            )

        self._commands[command_id] = CommandResultDTO(
            schema_version="1.0",
            command_id=command_id,
            command_type=CommandTypeEnum.UPDATE_CONFIG,
            stream_id=config.stream_id,
            status=CommandResultStatusEnum.APPLIED if changed else CommandResultStatusEnum.NOOP,
            changed=changed,
            processed_at=now,
        )

        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=command_id,
            stream_id=config.stream_id,
            status="ACCEPTED",
            message=f"Update config command accepted for stream {config.stream_id}",
        )

    async def get_status(self, stream_id: str) -> Optional[RuntimeStatusDTO]:
        return self._statuses.get(stream_id)

    async def get_command_result(self, command_id: str) -> CommandLookup:
        if command_id in self._commands:
            return CommandLookup(
                state=CommandLookupState.FINAL,
                result=self._commands[command_id],
            )
        return CommandLookup(state=CommandLookupState.MISSING)

    async def close(self) -> None:
        self._streams.clear()
        self._statuses.clear()
        self._commands.clear()


# Implementation giả sinh ra alert ảo theo từng stream độc lập để test giao diện WebSocket
class FakeAlertSource(AlertSource):
    def __init__(self, *, max_recent_per_stream: int = 100) -> None:
        if max_recent_per_stream <= 0:
            raise ValueError("max_recent_per_stream must be > 0")
        self._max_recent_per_stream = max_recent_per_stream
        self._recent: Dict[str, List[AlertDTO]] = {}
        self._subscribers: Dict[str, List[asyncio.Queue[AlertDTO | None]]] = {}
        self._stream_tasks: Dict[str, asyncio.Task] = {}
        self._pending_generators: Dict[str, Tuple[float, float]] = {}

    def _create_alert(
        self,
        stream_id: str,
        state: AlertState,
        event_type: EventType = EventType.BLACK_SCREEN,
        event_id: str | None = None,
        event_started_at: datetime | None = None,
    ) -> AlertDTO:
        now = datetime.now(timezone.utc)
        return AlertDTO(
            schema_version="1.0",
            alert_id=str(uuid.uuid4()),
            event_id=event_id or f"evt-{stream_id}-{uuid.uuid4()}",
            category=AlertCategory.CONTENT,
            event_type=event_type,
            state=state,
            stream_id=stream_id,
            occurred_at=now,
            emitted_at=now,
            event_started_at=event_started_at or now,
            event_ended_at=now if state == AlertState.RESOLVED else None,
            reason="fake_threshold_reached",
            attributes={},
        )

    async def publish_alert(self, alert: AlertDTO) -> None:
        if alert.stream_id not in self._recent:
            self._recent[alert.stream_id] = []
        self._recent[alert.stream_id].append(alert)
        del self._recent[alert.stream_id][:-self._max_recent_per_stream]
        for q in list(self._subscribers.get(alert.stream_id, [])):
            await q.put(alert)

    async def recent(self, stream_id: str, limit: int = 50) -> List[AlertDTO]:
        return self._recent.get(stream_id, [])[-limit:]

    async def subscribe(self, stream_id: str) -> AsyncGenerator[AlertDTO, None]:
        if stream_id not in self._subscribers:
            self._subscribers[stream_id] = []

        queue: asyncio.Queue[AlertDTO | None] = asyncio.Queue()
        self._subscribers[stream_id].append(queue)

        # Khởi chạy pending generator nếu có
        if stream_id in self._pending_generators:
            interval_s, recovery_s = self._pending_generators.pop(stream_id)
            self.start_generating_fake_alerts(stream_id, interval_s, recovery_s)

        try:
            while True:
                alert = await queue.get()
                if alert is None:
                    return
                yield alert
        finally:
            if stream_id in self._subscribers and queue in self._subscribers[stream_id]:
                self._subscribers[stream_id].remove(queue)

    def start_generating_fake_alerts(
        self,
        stream_id: str,
        interval_seconds: float = 10.0,
        recovery_seconds: float = 5.0,
    ) -> None:
        if stream_id in self._stream_tasks and not self._stream_tasks[stream_id].done():
            return

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._generate_alerts_loop(stream_id, interval_seconds, recovery_seconds)
            )
            self._stream_tasks[stream_id] = task
        except RuntimeError:
            self._pending_generators[stream_id] = (interval_seconds, recovery_seconds)

    async def _generate_alerts_loop(
        self,
        stream_id: str,
        interval_seconds: float,
        recovery_seconds: float,
    ) -> None:
        if stream_id not in self._recent:
            self._recent[stream_id] = []

        try:
            while True:
                await asyncio.sleep(interval_seconds)
                # Phát alert OPEN
                alert_open = self._create_alert(stream_id, AlertState.OPEN, EventType.BLACK_SCREEN)
                await self.publish_alert(alert_open)

                await asyncio.sleep(recovery_seconds)
                # Phát alert RESOLVED
                alert_resolved = self._create_alert(
                    stream_id,
                    AlertState.RESOLVED,
                    EventType.BLACK_SCREEN,
                    event_id=alert_open.event_id,
                    event_started_at=alert_open.event_started_at,
                )
                await self.publish_alert(alert_resolved)
        except asyncio.CancelledError:
            pass

    async def stop_stream(self, stream_id: str) -> None:
        self._pending_generators.pop(stream_id, None)
        task = self._stream_tasks.pop(stream_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        subscribers = self._subscribers.pop(stream_id, [])
        for queue in subscribers:
            queue.put_nowait(None)

    async def close(self) -> None:
        self._pending_generators.clear()
        tasks = list(self._stream_tasks.values())
        self._stream_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        subscribers = list(self._subscribers.values())
        self._subscribers.clear()
        for queues in subscribers:
            for queue in queues:
                queue.put_nowait(None)
