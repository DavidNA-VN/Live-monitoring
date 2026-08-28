from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Optional
import uuid

import redis.exceptions

from core.redis_keys import ControlRedisKeys
from .base import MonitoringControl
from .errors import PresentationConflictError, PresentationServiceUnavailableError
from .redis_command_codec import (
    build_command_payload,
    build_submission_record,
    compute_logical_fingerprint,
    parse_submission_record,
)
from ..models import (
    CommandSubmissionDTO,
    CommandTypeEnum,
    StreamConfigDTO,
)

logger = logging.getLogger(__name__)

# Lua script thực hiện kiểm tra idempotency, lưu submitted marker và XADD command một cách atomic
SUBMIT_COMMAND_LUA = """
local idempotency_key = KEYS[1]
local submitted_key = KEYS[2]
local stream_key = KEYS[3]

local fingerprint = ARGV[1]
local submission_json = ARGV[2]
local command_json = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])
local has_idempotency = ARGV[5]

local function has_type(key, expected)
    local actual = redis.call("TYPE", key)["ok"]
    return actual == "none" or actual == expected
end

if has_idempotency == "1" and not has_type(idempotency_key, "string") then
    return {3, "INVALID_IDEMPOTENCY_KEY_TYPE"}
end
if not has_type(submitted_key, "string") then
    return {3, "INVALID_SUBMITTED_KEY_TYPE"}
end
if not has_type(stream_key, "stream") then
    return {3, "INVALID_COMMAND_STREAM_TYPE"}
end

if has_idempotency == "1" then
    local existing = redis.call("GET", idempotency_key)
    if existing then
        local ok, receipt = pcall(cjson.decode, existing)
        if ok and receipt and receipt["fingerprint"] == fingerprint then
            return {1, existing}
        else
            return {2, "IDEMPOTENCY_CONFLICT"}
        end
    end
end

redis.call("XADD", stream_key, "*", "payload", command_json)
redis.call("SET", submitted_key, submission_json, "EX", ttl_seconds)
if has_idempotency == "1" then
    redis.call("SET", idempotency_key, submission_json, "EX", ttl_seconds)
end

return {0, submission_json}
"""


class MonitoringControlError(Exception):
    """Base exception cho RedisMonitoringControl."""
    pass


class IdempotencyConflictError(MonitoringControlError, PresentationConflictError):
    """Lỗi khi tái sử dụng Idempotency-Key với payload khác."""
    pass


class ControlRedisUnavailableError(
    MonitoringControlError,
    PresentationServiceUnavailableError,
):
    """Lỗi khi Redis mất kết nối hoặc timeout."""
    pass


class RedisMonitoringControl(MonitoringControl):
    """
    Adapter gửi command điều khiển stream vào Redis Stream thông qua redis.asyncio.
    Hỗ trợ idempotency token và kiểm soát race condition bằng Lua script atomic.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: Optional[ControlRedisKeys] = None,
        submission_ttl_seconds: int = 86400,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        command_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        if submission_ttl_seconds <= 0:
            raise ValueError("submission_ttl_seconds must be > 0")
        self.redis = redis_client
        self.keys = keys or ControlRedisKeys()
        self.submission_ttl_seconds = submission_ttl_seconds
        self.utc_now = utc_now
        self.command_id_factory = command_id_factory
        self._script: Any = None

    async def _get_script(self) -> Any:
        if self._script is None:
            self._script = self.redis.register_script(SUBMIT_COMMAND_LUA)
        return self._script

    async def _submit(
        self,
        command_type: CommandTypeEnum,
        stream_id: str,
        *,
        config: Optional[StreamConfigDTO] = None,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        if not stream_id or not stream_id.strip():
            raise ValueError("stream_id must not be empty")
        if idempotency_key is not None:
            if not idempotency_key.strip():
                raise ValueError("idempotency_key must not be empty")
            if len(idempotency_key) > 256:
                raise ValueError("idempotency_key must not exceed 256 characters")

        now = self.utc_now()
        command_id = self.command_id_factory()
        if not command_id:
            raise ValueError("command_id_factory returned an empty command_id")

        # Build payload chuẩn canonical
        command_dict = build_command_payload(
            command_id=command_id,
            command_type=command_type,
            stream_id=stream_id,
            requested_at=now,
            config=config,
        )
        command_json = json.dumps(command_dict, separators=(",", ":"))

        # Compute logical fingerprint
        fingerprint = compute_logical_fingerprint(
            command_type=command_type,
            stream_id=stream_id,
            config=config,
        )

        submission_dict = build_submission_record(
            command_id=command_id,
            stream_id=stream_id,
            fingerprint=fingerprint,
            requested_at=now,
        )
        submission_json = json.dumps(submission_dict, separators=(",", ":"))

        submitted_redis_key = self.keys.submitted_command(command_id)
        idempotency_redis_key = (
            self.keys.idempotency_key(idempotency_key)
            if idempotency_key
            else submitted_redis_key
        )
        commands_stream_key = self.keys.commands()

        keys = [idempotency_redis_key, submitted_redis_key, commands_stream_key]
        args = [
            fingerprint,
            submission_json,
            command_json,
            str(self.submission_ttl_seconds),
            "1" if idempotency_key else "0",
        ]

        try:
            script = await self._get_script()
            result = await script(keys=keys, args=args)
        except redis.exceptions.RedisError as exc:
            logger.error("Redis command submission failed: %s", exc)
            raise ControlRedisUnavailableError(f"Redis unavailable: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error during Redis command submission: %s", exc)
            raise ControlRedisUnavailableError(f"Unexpected Redis error: {exc}") from exc

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            raise ControlRedisUnavailableError("Invalid response from submission Lua script")

        status_code, payload_data = result[0], result[1]
        if isinstance(payload_data, bytes):
            payload_data = payload_data.decode("utf-8")

        if status_code == 2:
            raise IdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' was already used with a different command payload"
            )
        if status_code == 3:
            logger.error("Redis command key has an incompatible type: %s", payload_data)
            raise ControlRedisUnavailableError(
                "Redis command key has an incompatible type"
            )

        try:
            record = json.loads(payload_data) if isinstance(payload_data, str) else payload_data
            submission = parse_submission_record(record)
            if status_code == 1:
                logger.info(
                    "Idempotent command replay for stream %s command_id=%s",
                    stream_id,
                    submission.command_id,
                )
            return submission
        except Exception as exc:
            raise ControlRedisUnavailableError(f"Failed to parse submission receipt: {exc}") from exc

    async def start_stream(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        return await self._submit(
            CommandTypeEnum.START,
            config.stream_id,
            config=config,
            idempotency_key=idempotency_key,
        )

    async def pause_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        return await self._submit(
            CommandTypeEnum.PAUSE,
            stream_id,
            config=None,
            idempotency_key=idempotency_key,
        )

    async def resume_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        return await self._submit(
            CommandTypeEnum.RESUME,
            stream_id,
            config=None,
            idempotency_key=idempotency_key,
        )

    async def stop_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        return await self._submit(
            CommandTypeEnum.STOP,
            stream_id,
            config=None,
            idempotency_key=idempotency_key,
        )

    async def update_config(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        return await self._submit(
            CommandTypeEnum.UPDATE_CONFIG,
            config.stream_id,
            config=config,
            idempotency_key=idempotency_key,
        )

    async def close(self) -> None:
        # Note: Connection lifecycle is managed by caller or FastAPI lifespan
        pass
