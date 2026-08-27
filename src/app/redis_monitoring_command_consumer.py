from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from threading import Event
from typing import Any, Callable, Protocol

import redis

from app.stream_config_mapper import StreamConfigMappingError
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

logger = logging.getLogger(__name__)


class _CommandDeferred(Exception):
    def __init__(self, entry_id: str, fields: dict[str, object]) -> None:
        super().__init__(f"Command entry '{entry_id}' deferred")
        self.entry_id = entry_id
        self.fields = fields


class MonitoringCommandExecutor(Protocol):
    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
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
        self.block_milliseconds = block_milliseconds
        self.claim_idle_milliseconds = claim_idle_milliseconds
        self.batch_size = batch_size
        self.result_max_length = result_max_length
        self.dead_letter_max_length = dead_letter_max_length
        self.processed_ttl_seconds = processed_ttl_seconds
        self.on_command_finalized = on_command_finalized
        self.poll_retry_backoff = poll_retry_backoff
        self._ready_event = Event()
        self._deferred_entries: list[tuple[str, dict[str, object]]] = []
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
                    logger.exception("Monitoring command Redis poll failed")
                    stop_event.wait(self.poll_retry_backoff)
        finally:
            self._ready_event.clear()

    def poll_once(self, stop_event: Event | None = None) -> int:
        if stop_event is not None and stop_event.is_set():
            return 0

        processing_deferred = bool(self._deferred_entries)
        self._waiting_for_pending_claim = False
        if processing_deferred:
            entries = list(self._deferred_entries)
        else:
            entries = self._read_own_pending()
            if not entries:
                entries = self._claim_abandoned_pending()
            if not entries:
                if self._pending_count() > 0:
                    self._waiting_for_pending_claim = True
                    entries = []
                else:
                    entries = self._read_new_entries()

        processed = 0
        for index, (entry_id, fields) in enumerate(entries):
            if stop_event is not None and stop_event.is_set() and index > 0:
                self._deferred_entries = [
                    *self._deferred_entries,
                    *entries[index:],
                ]
                break

            try:
                self._process(entry_id, fields)
            except _CommandDeferred as deferred:
                self._deferred_entries = [
                    (deferred.entry_id, deferred.fields),
                    *entries[index + 1 :],
                ]
                break
            else:
                if processing_deferred and self._deferred_entries:
                    self._deferred_entries.pop(0)
                processed += 1
        return processed

    def _read_own_pending(self) -> list[tuple[str, dict[str, str]]]:
        response = self.redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.keys.commands(): "0"},
            count=self.batch_size,
        )
        return response[0][1] if response else []

    def _claim_abandoned_pending(self) -> list[tuple[str, dict[str, str]]]:
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
        return response[1]

    def _read_new_entries(self) -> list[tuple[str, dict[str, str]]]:
        response = self.redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.keys.commands(): ">"},
            count=self.batch_size,
            block=self.block_milliseconds,
        )
        return response[0][1] if response else []

    def _pending_count(self) -> int:
        summary = self.redis.xpending(
            self.keys.commands(),
            self.group_name,
        )
        if isinstance(summary, dict):
            return int(summary.get("pending", 0))
        if isinstance(summary, (tuple, list)) and summary:
            return int(summary[0])
        return 0

    def _process(self, entry_id: str, fields: dict[str, object]) -> None:
        try:
            payload = self._field(fields, "payload")
            command = MonitoringCommand.from_json(payload)
        except MonitoringCommandError as exc:
            self._dead_letter(
                entry_id,
                self._text(fields.get("payload", "")),
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
                self._dead_letter(
                    entry_id,
                    payload,
                    "DUPLICATE_COMMAND_ID",
                    "command_id was already used by a different payload",
                )
                return
            self.redis.xack(self.keys.commands(), self.group_name, entry_id)
            return

        try:
            result = self.handler.handle(command)
            self._finalize(entry_id, processed_key, fingerprint, result)
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
            self._finalize(entry_id, processed_key, fingerprint, result)
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
            self._finalize(entry_id, processed_key, fingerprint, result)
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
            )

    def _finalize(
        self,
        entry_id: str,
        processed_key: str,
        fingerprint: str,
        result: MonitoringCommandResult,
        *,
        dead_letter: bool = False,
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
                "failed_at": datetime.now(timezone.utc).isoformat(),
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
            processed_at=datetime.now(timezone.utc),
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
