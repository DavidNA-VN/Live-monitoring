from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from threading import Event
import time
from typing import Any, Callable, Protocol

import redis

from app.command_guardrails import CommandGuardrails
from app.stream_config_mapper import StreamConfigMappingError
from core.command_metrics import CommandMetricsCollector
from core.desired_state_repository import DesiredStatePersistenceError
from core.monitoring_control import (
    MonitoringControlError,
    StreamAlreadyExistsError,
    StreamIdentityMismatchError,
    StreamNotFoundError,
)
from core.redis_keys import ControlRedisKeys
from models.monitoring_command import (
    MonitoringCommand,
    MonitoringCommandError,
    MonitoringCommandResult,
    MonitoringCommandResultStatus,
)
from models.command_metrics import CommandMetricsSnapshot

logger = logging.getLogger(__name__)


class _CommandDeferred(Exception):
    def __init__(self, entry_id: str, fields: dict[str, object]) -> None:
        super().__init__(f"Command entry '{entry_id}' deferred")
        self.entry_id = entry_id
        self.fields = fields


class MonitoringCommandExecutor(Protocol):
    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
        ...


class CommandMetricsPublisher(Protocol):
    def publish(self, snapshot: CommandMetricsSnapshot) -> bool:
        ...


class RedisMonitoringCommandConsumer:
    def __init__(
        self,
        *,
        redis_client: Any,
        handler: MonitoringCommandExecutor,
        keys: ControlRedisKeys | None = None,
        group_name: str = "monitoring-workers",
        consumer_name: str = "worker-1",
        worker_id: str = "worker-1",
        guardrails: CommandGuardrails | None = None,
        metrics: CommandMetricsCollector | None = None,
        metrics_publisher: CommandMetricsPublisher | None = None,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        block_milliseconds: int = 1_000,
        claim_idle_milliseconds: int = 30_000,
        batch_size: int = 10,
        result_max_length: int = 10_000,
        dead_letter_max_length: int = 1_000,
        processed_ttl_seconds: int = 86_400,
        on_command_finalized: Callable[[], None] | None = None,
        poll_retry_backoff: float = 1.0,
    ) -> None:
        positive = {
            "block_milliseconds": block_milliseconds,
            "claim_idle_milliseconds": claim_idle_milliseconds,
            "batch_size": batch_size,
            "result_max_length": result_max_length,
            "dead_letter_max_length": dead_letter_max_length,
            "processed_ttl_seconds": processed_ttl_seconds,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
        if poll_retry_backoff <= 0:
            raise ValueError("poll_retry_backoff must be > 0")
        if not group_name or not consumer_name:
            raise ValueError("group_name and consumer_name must not be empty")
        self.redis = (
            redis_client.client
            if hasattr(redis_client, "client")
            else redis_client
        )
        self.handler = handler
        self.keys = keys or ControlRedisKeys()
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.worker_id = worker_id
        self.guardrails = guardrails or CommandGuardrails()
        self.metrics = metrics
        self.metrics_publisher = metrics_publisher
        self.clock = clock
        self.utc_now = utc_now
        self.block_milliseconds = block_milliseconds
        self.claim_idle_milliseconds = claim_idle_milliseconds
        self.batch_size = batch_size
        self.result_max_length = result_max_length
        self.dead_letter_max_length = dead_letter_max_length
        self.processed_ttl_seconds = processed_ttl_seconds
        self.on_command_finalized = on_command_finalized
        self.poll_retry_backoff = poll_retry_backoff
        self._ready_event = Event()
        self._deferred_entries: list[
            tuple[str, dict[str, object], bool]
        ] = []
        self._waiting_for_pending_claim = False

    @property
    def is_ready(self) -> bool:
        return self._ready_event.is_set()

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(
                self.keys.commands(),
                self.group_name,
                id="0-0",
                mkstream=True,
            )
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def run(self, stop_event: Event) -> None:
        group_ready = False
        try:
            while not stop_event.is_set():
                try:
                    if not group_ready:
                        self.ensure_group()
                        group_ready = True
                        self._ready_event.set()
                    self.poll_once(stop_event)
                    if self._deferred_entries or self._waiting_for_pending_claim:
                        stop_event.wait(self.poll_retry_backoff)
                except redis.RedisError:
                    self._ready_event.clear()
                    group_ready = False
                    if self.metrics is not None:
                        self.metrics.record_poll_failure(self.utc_now())
                    logger.exception("Monitoring command Redis poll failed")
                    stop_event.wait(self.poll_retry_backoff)
        finally:
            self._ready_event.clear()

    def poll_once(self, stop_event: Event | None = None) -> int:
        if stop_event is not None and stop_event.is_set():
            return 0

        processing_deferred = bool(self._deferred_entries)
        self._waiting_for_pending_claim = False
        is_reclaimed = False
        pending_count: int | None = None
        if processing_deferred:
            entries = list(self._deferred_entries)
        else:
            raw_entries = self._read_own_pending()
            if not raw_entries:
                raw_entries = self._claim_abandoned_pending()
                if raw_entries:
                    is_reclaimed = True
                    if self.metrics is not None:
                        self.metrics.record_reclaimed(len(raw_entries))
            if not raw_entries:
                pending_count = self._pending_count()
                if pending_count > 0:
                    self._waiting_for_pending_claim = True
                    raw_entries = []
                else:
                    raw_entries = self._read_new_entries()
            entries = [
                (entry_id, fields, is_reclaimed)
                for entry_id, fields in raw_entries
            ]

        if self.metrics is not None:
            if pending_count is None:
                pending_count = self._pending_count()
            self.metrics.record_poll_success(
                pending_count=pending_count,
                deferred_count=len(self._deferred_entries),
                at=self.utc_now(),
            )
            if entries and not processing_deferred:
                self.metrics.record_delivery(len(entries))

        processed = 0
        for index, (entry_id, fields, entry_reclaimed) in enumerate(entries):
            if stop_event is not None and stop_event.is_set() and index > 0:
                self._deferred_entries = [
                    *self._deferred_entries,
                    *entries[index:],
                ]
                break

            try:
                self._process(
                    entry_id,
                    fields,
                    is_reclaimed=entry_reclaimed,
                )
                processed += 1
                if processing_deferred and self._deferred_entries:
                    self._deferred_entries.pop(0)
            except _CommandDeferred:
                if not processing_deferred:
                    self._deferred_entries = [
                        (entry_id, fields, entry_reclaimed),
                        *entries[index + 1:],
                    ]
                break

        if self.metrics is not None and self.metrics_publisher is not None:
            snapshot = self.metrics.snapshot(self.worker_id, self.utc_now())
            self.metrics_publisher.publish(snapshot)

        return processed

    def _read_own_pending(self) -> list[tuple[str, dict[str, object]]]:
        response = self.redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.keys.commands(): "0"},
            count=self.batch_size,
        )
        return self._extract_entries(response)

    def _claim_abandoned_pending(self) -> list[tuple[str, dict[str, object]]]:
        response = self.redis.xautoclaim(
            self.keys.commands(),
            self.group_name,
            self.consumer_name,
            self.claim_idle_milliseconds,
            "0-0",
            count=self.batch_size,
        )
        if not response or len(response) < 2:
            return []
        raw_entries = response[1]
        entries: list[tuple[str, dict[str, object]]] = []
        for item in raw_entries:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                entry_id, fields = item
                entries.append((self._text(entry_id), fields))
        return entries

    def _pending_count(self) -> int:
        pending_info = self.redis.xpending(
            self.keys.commands(),
            self.group_name,
        )
        if isinstance(pending_info, dict):
            return int(pending_info.get("pending", 0))
        if isinstance(pending_info, (tuple, list)) and pending_info:
            return int(pending_info[0])
        return 0

    def _read_new_entries(self) -> list[tuple[str, dict[str, object]]]:
        response = self.redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.keys.commands(): ">"},
            count=self.batch_size,
            block=self.block_milliseconds,
        )
        return self._extract_entries(response)

    def _extract_entries(
        self, response: list[Any]
    ) -> list[tuple[str, dict[str, object]]]:
        if not response:
            return []
        entries: list[tuple[str, dict[str, object]]] = []
        for _stream_name, stream_entries in response:
            for entry_id, fields in stream_entries:
                entries.append((self._text(entry_id), fields))
        return entries

    def _process(
        self,
        entry_id: str,
        fields: dict[str, object],
        *,
        is_reclaimed: bool = False,
    ) -> None:
        t0 = self.clock()
        try:
            raw_payload = self._field(fields, "payload")
        except MonitoringCommandError as exc:
            if self.metrics is not None:
                self.metrics.record_dead_letter("INVALID_PAYLOAD")
            self._dead_letter(
                entry_id,
                self._text(fields.get("payload", "")),
                "INVALID_PAYLOAD",
                str(exc),
            )
            return

        # Guardrail 1: Byte size check
        valid_size, size_bytes = self.guardrails.check_payload_size(raw_payload)
        if not valid_size:
            logger.warning(
                "Rejecting oversized monitoring command payload entry_id=%s size_bytes=%d max_bytes=%d",
                entry_id,
                size_bytes,
                self.guardrails.max_payload_bytes,
            )
            if self.metrics is not None:
                self.metrics.record_dead_letter("COMMAND_PAYLOAD_TOO_LARGE", oversized=True)
            self._dead_letter_oversized(
                entry_id,
                size_bytes,
                "COMMAND_PAYLOAD_TOO_LARGE",
                "Command payload exceeds maximum allowed size",
            )
            return

        # JSON parsing
        try:
            command = MonitoringCommand.from_json(raw_payload)
        except MonitoringCommandError as exc:
            if self.metrics is not None:
                self.metrics.record_dead_letter("INVALID_COMMAND")
            self._dead_letter(
                entry_id,
                raw_payload,
                "INVALID_COMMAND",
                str(exc),
            )
            return

        processed_key = self.keys.processed_command(command.command_id)
        canonical = json.dumps(
            command.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        )
        fingerprint = sha256(canonical.encode("utf-8")).hexdigest()
        processed = self.redis.get(processed_key)
        if processed is not None:
            try:
                marker = json.loads(self._text(processed))
            except json.JSONDecodeError:
                marker = {}
            if marker.get("fingerprint") != fingerprint:
                if self.metrics is not None:
                    self.metrics.record_dead_letter("DUPLICATE_COMMAND_ID")
                self._dead_letter(
                    entry_id,
                    raw_payload,
                    "DUPLICATE_COMMAND_ID",
                    "command_id was already used by a different payload",
                )
                return
            if self.metrics is not None:
                self.metrics.record_duplicate_replay()
            self.redis.xack(self.keys.commands(), self.group_name, entry_id)
            action_name = command.action.value if hasattr(command.action, "value") else str(command.action)
            logger.info(
                "Monitoring command duplicate replay acknowledged worker_id=%s consumer_name=%s command_id=%s stream_id=%s action=%s",
                self.worker_id,
                self.consumer_name,
                command.command_id,
                command.stream_id,
                action_name,
            )
            return

        # Guardrail 2: Command age check
        valid_age, age_error = self.guardrails.check_command_age(
            command.requested_at,
            now=self.utc_now(),
            is_reclaimed=is_reclaimed,
        )
        if not valid_age:
            if self.metrics is not None and age_error == "STALE_COMMAND":
                self.metrics.record_stale()
            result = self._failure_result(
                command,
                MonitoringCommandResultStatus.REJECTED,
                age_error or "STALE_COMMAND",
                (
                    "Command timestamp is too far in the future"
                    if age_error == "FUTURE_COMMAND"
                    else "Command age exceeded maximum allowed threshold"
                ),
            )
            self._finalize(entry_id, processed_key, fingerprint, result, t0=t0)
            return

        try:
            result = self.handler.handle(command)
            self._finalize(entry_id, processed_key, fingerprint, result, t0=t0)
        except DesiredStatePersistenceError:
            logger.error(
                "Desired state persistence failed for command %s; leaving command pending in Redis",
                command.command_id,
                exc_info=True,
            )
            raise _CommandDeferred(entry_id, fields)
        except redis.RedisError:
            raise
        except (StreamConfigMappingError, ValueError) as exc:
            result = self._failure_result(
                command,
                MonitoringCommandResultStatus.REJECTED,
                type(exc).__name__,
                str(exc),
            )
            self._finalize(entry_id, processed_key, fingerprint, result, t0=t0)
        except MonitoringControlError as exc:
            rejected = isinstance(
                exc,
                (
                    StreamAlreadyExistsError,
                    StreamNotFoundError,
                    StreamIdentityMismatchError,
                ),
            )
            result = self._failure_result(
                command,
                (
                    MonitoringCommandResultStatus.REJECTED
                    if rejected
                    else MonitoringCommandResultStatus.FAILED
                ),
                type(exc).__name__,
                str(exc),
            )
            self._finalize(entry_id, processed_key, fingerprint, result, t0=t0)
        except Exception as exc:
            logger.exception(
                "Unexpected monitoring command failure command_id=%s",
                command.command_id,
            )
            result = self._failure_result(
                command,
                MonitoringCommandResultStatus.FAILED,
                "UNEXPECTED_ERROR",
                "Internal monitoring operation failed",
            )
            self._finalize(
                entry_id,
                processed_key,
                fingerprint,
                result,
                dead_letter=True,
                t0=t0,
            )

    def _finalize(
        self,
        entry_id: str,
        processed_key: str,
        fingerprint: str,
        result: MonitoringCommandResult,
        *,
        dead_letter: bool = False,
        t0: float | None = None,
    ) -> None:
        payload = json.dumps(result.to_dict(), separators=(",", ":"))
        marker = json.dumps(
            {"fingerprint": fingerprint, "result": result.to_dict()},
            separators=(",", ":"),
        )
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.set(
            processed_key,
            marker,
            ex=self.processed_ttl_seconds,
        )
        pipeline.xadd(
            self.keys.command_results(),
            {"payload": payload},
            maxlen=self.result_max_length,
            approximate=False,
        )
        if dead_letter:
            pipeline.xadd(
                self.keys.dead_letter(),
                {
                    "source_entry_id": entry_id,
                    "payload": payload,
                    "error_code": result.error_code or "FAILED",
                    "error": result.error or "",
                    "failed_at": result.processed_at.isoformat(),
                },
                maxlen=self.dead_letter_max_length,
                approximate=False,
            )
        pipeline.xack(self.keys.commands(), self.group_name, entry_id)
        pipeline.execute()

        duration_ms = max(0.0, (self.clock() - t0) * 1000.0) if t0 is not None else 0.0
        if self.metrics is not None:
            st = (
                result.status.value
                if hasattr(result.status, "value")
                else str(result.status)
            )
            self.metrics.record_result(
                st,
                result.changed,
                duration_ms,
                result.error_code,
                result.processed_at,
            )
            if dead_letter:
                self.metrics.record_dead_letter(result.error_code or "FAILED")

        action_name = (
            result.action.value
            if hasattr(result.action, "value")
            else str(result.action)
        )
        status_name = (
            result.status.value
            if hasattr(result.status, "value")
            else str(result.status)
        )
        logger.info(
            "Monitoring command finalized worker_id=%s consumer_name=%s command_id=%s stream_id=%s action=%s status=%s changed=%s duration_ms=%.2f error_code=%s",
            self.worker_id,
            self.consumer_name,
            result.command_id,
            result.stream_id,
            action_name,
            status_name,
            result.changed,
            duration_ms,
            result.error_code or "NONE",
        )

        if self.on_command_finalized is not None:
            try:
                self.on_command_finalized()
            except Exception:
                logger.exception("Failed to invoke on_command_finalized callback")

    def _dead_letter(
        self,
        entry_id: str,
        payload: str,
        error_code: str,
        error_message: str,
    ) -> None:
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.xadd(
            self.keys.dead_letter(),
            {
                "source_entry_id": entry_id,
                "payload": payload,
                "error_code": error_code,
                "error": error_message,
                "failed_at": self.utc_now().isoformat(),
            },
            maxlen=self.dead_letter_max_length,
            approximate=False,
        )
        pipeline.xack(self.keys.commands(), self.group_name, entry_id)
        pipeline.execute()

    def _dead_letter_oversized(
        self,
        entry_id: str,
        size_bytes: int,
        error_code: str,
        error_message: str,
    ) -> None:
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.xadd(
            self.keys.dead_letter(),
            {
                "source_entry_id": entry_id,
                "payload_size_bytes": str(size_bytes),
                "error_code": error_code,
                "error": error_message,
                "failed_at": self.utc_now().isoformat(),
            },
            maxlen=self.dead_letter_max_length,
            approximate=False,
        )
        pipeline.xack(self.keys.commands(), self.group_name, entry_id)
        pipeline.execute()

    def _failure_result(
        self,
        command: MonitoringCommand,
        status: MonitoringCommandResultStatus,
        error_code: str,
        error_message: str,
    ) -> MonitoringCommandResult:
        return MonitoringCommandResult(
            command_id=command.command_id,
            action=command.action,
            stream_id=command.stream_id,
            status=status,
            changed=False,
            processed_at=self.utc_now(),
            error_code=error_code,
            error=error_message,
        )

    def _field(self, fields: dict[str, object], name: str) -> str:
        for key, value in fields.items():
            if self._text(key) == name:
                return self._text(value)
        raise MonitoringCommandError(f"Missing '{name}' field in stream entry")

    def _text(self, value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
