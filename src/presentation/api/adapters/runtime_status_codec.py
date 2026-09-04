from datetime import datetime
import json
import math
import re
from typing import Any, Dict, Set

from pydantic import ValidationError

from ..models import (
    RuntimeStatusDTO,
    StreamHealthEnum,
    StreamStatusEnum,
)

WORKER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
WORKER_ID_REGEX = re.compile(WORKER_ID_PATTERN)

ALLOWED_KEYS: Set[str] = {
    "schema_version",
    "stream_id",
    "status",
    "health",
    "started_at",
    "last_poll_at",
    "active_variant_count",
    "queue_depth",
    "queue_lag_seconds",
    "live_edge_lag_seconds",
    "error",
    "telemetry_available",
    "health_reasons",
    "checks",
    "worker_id",
    "observed_at",
}

REQUIRED_KEYS: Set[str] = {
    "schema_version",
    "stream_id",
    "status",
    "health",
    "active_variant_count",
    "queue_depth",
    "checks",
    "worker_id",
    "observed_at",
}


class RuntimeStatusCodecError(ValueError):
    """Lỗi khi parse hoặc validate public runtime status snapshot từ Redis."""
    pass


def _parse_strict_iso_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeStatusCodecError(f"Field '{field_name}' must be an ISO-8601 string, got {type(value).__name__}")
    try:
        dt = datetime.fromisoformat(value)
    except Exception as exc:
        raise RuntimeStatusCodecError(f"Field '{field_name}' has invalid ISO-8601 datetime format: {exc}") from exc

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise RuntimeStatusCodecError(f"Field '{field_name}' must include timezone offset, naive datetime is rejected")
    return dt


def parse_public_runtime_status(
    raw_data: bytes | str | Dict[str, Any],
    *,
    requested_stream_id: str,
) -> RuntimeStatusDTO:
    """
    Parse và validate cực kỳ nghiêm ngặt public runtime status snapshot từ Redis hash.
    Loại bỏ hoàn toàn type-coercion nguy hiểm và cấm extra fields.
    """
    if not requested_stream_id or not requested_stream_id.strip():
        raise RuntimeStatusCodecError("requested_stream_id must not be empty")

    if isinstance(raw_data, bytes):
        try:
            raw_text = raw_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeStatusCodecError(f"Failed to decode UTF-8 bytes: {exc}") from exc
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeStatusCodecError(f"Invalid JSON syntax in runtime status payload: {exc}") from exc
    elif isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise RuntimeStatusCodecError(f"Invalid JSON syntax in runtime status payload: {exc}") from exc
    elif isinstance(raw_data, dict):
        data = raw_data
    else:
        raise RuntimeStatusCodecError(f"Expected bytes, str or dict for runtime status, got {type(raw_data).__name__}")

    if not isinstance(data, dict):
        raise RuntimeStatusCodecError(f"Runtime status payload must be a JSON object, got {type(data).__name__}")

    # 1. Cấm tuyệt đối extra fields ngoài schema (ví dụ: storage_id, internal_metrics, redis_key)
    extra_keys = set(data.keys()) - ALLOWED_KEYS
    if extra_keys:
        raise RuntimeStatusCodecError(f"Unexpected extra fields in runtime status payload: {sorted(extra_keys)}")

    # 2. Kiểm tra bắt buộc các trường required
    missing_keys = REQUIRED_KEYS - set(data.keys())
    if missing_keys:
        raise RuntimeStatusCodecError(f"Missing required fields in runtime status payload: {sorted(missing_keys)}")

    # 3. Validate schema_version
    if data["schema_version"] != "1.0":
        raise RuntimeStatusCodecError(f"Unsupported schema_version: expected '1.0', got {data['schema_version']!r}")

    # 4. Validate stream_id và đối chiếu với stream ID đang truy vấn
    stream_id = data["stream_id"]
    if not isinstance(stream_id, str) or not stream_id.strip():
        raise RuntimeStatusCodecError("Field 'stream_id' must be a non-empty string")
    if stream_id != requested_stream_id:
        raise RuntimeStatusCodecError(
            f"Mismatched stream_id: payload contains '{stream_id}' but query requested '{requested_stream_id}'"
        )

    # 5. Validate status
    raw_status = data["status"]
    if not isinstance(raw_status, str):
        raise RuntimeStatusCodecError(f"Field 'status' must be a string, got {type(raw_status).__name__}")
    try:
        status = StreamStatusEnum(raw_status)
    except ValueError as exc:
        raise RuntimeStatusCodecError(f"Invalid stream status '{raw_status}': {exc}") from exc

    # 6. Validate health
    raw_health = data["health"]
    if not isinstance(raw_health, str):
        raise RuntimeStatusCodecError(f"Field 'health' must be a string, got {type(raw_health).__name__}")
    try:
        health = StreamHealthEnum(raw_health)
    except ValueError as exc:
        raise RuntimeStatusCodecError(f"Invalid stream health '{raw_health}': {exc}") from exc

    # 7. Validate anti-coercion integers: active_variant_count, queue_depth
    raw_active_variants = data["active_variant_count"]
    if isinstance(raw_active_variants, bool) or not isinstance(raw_active_variants, int):
        raise RuntimeStatusCodecError(
            f"Field 'active_variant_count' must be an integer, got {type(raw_active_variants).__name__}"
        )
    if raw_active_variants < 0:
        raise RuntimeStatusCodecError(f"Field 'active_variant_count' cannot be negative, got {raw_active_variants}")

    raw_queue_depth = data["queue_depth"]
    if isinstance(raw_queue_depth, bool) or not isinstance(raw_queue_depth, int):
        raise RuntimeStatusCodecError(f"Field 'queue_depth' must be an integer, got {type(raw_queue_depth).__name__}")
    if raw_queue_depth < 0:
        raise RuntimeStatusCodecError(f"Field 'queue_depth' cannot be negative, got {raw_queue_depth}")

    # 8. Validate optional queue_lag_seconds (must be finite, not boolean, >= 0)
    raw_lag = data.get("queue_lag_seconds")
    queue_lag_seconds: float | None = None
    if raw_lag is not None:
        if isinstance(raw_lag, bool) or not isinstance(raw_lag, (int, float)):
            raise RuntimeStatusCodecError(f"Field 'queue_lag_seconds' must be a number, got {type(raw_lag).__name__}")
        if not math.isfinite(raw_lag):
            raise RuntimeStatusCodecError(f"Field 'queue_lag_seconds' must be finite, got {raw_lag}")
        if raw_lag < 0:
            raise RuntimeStatusCodecError(f"Field 'queue_lag_seconds' cannot be negative, got {raw_lag}")
        queue_lag_seconds = float(raw_lag)

    raw_live_lag = data.get("live_edge_lag_seconds")
    live_edge_lag_seconds: float | None = None
    if raw_live_lag is not None:
        if isinstance(raw_live_lag, bool) or not isinstance(
            raw_live_lag, (int, float)
        ):
            raise RuntimeStatusCodecError(
                "Field 'live_edge_lag_seconds' must be a number"
            )
        if not math.isfinite(raw_live_lag) or raw_live_lag < 0:
            raise RuntimeStatusCodecError(
                "Field 'live_edge_lag_seconds' must be finite and >= 0"
            )
        live_edge_lag_seconds = float(raw_live_lag)

    # 9. Validate telemetry_available
    raw_telemetry = data.get("telemetry_available", False)
    if not isinstance(raw_telemetry, bool):
        raise RuntimeStatusCodecError(
            f"Field 'telemetry_available' must be a boolean, got {type(raw_telemetry).__name__}"
        )

    # 10. Validate health_reasons
    raw_reasons = data.get("health_reasons", [])
    if not isinstance(raw_reasons, list):
        raise RuntimeStatusCodecError(f"Field 'health_reasons' must be a list, got {type(raw_reasons).__name__}")
    for idx, reason in enumerate(raw_reasons):
        if not isinstance(reason, str):
            raise RuntimeStatusCodecError(
                f"Item at index {idx} in 'health_reasons' must be a string, got {type(reason).__name__}"
            )

    # 11. Validate checks
    raw_checks = data["checks"]
    if not isinstance(raw_checks, dict):
        raise RuntimeStatusCodecError(f"Field 'checks' must be a dictionary, got {type(raw_checks).__name__}")
    checks: Dict[str, Any] = {}
    for check_name, check_val in raw_checks.items():
        if not isinstance(check_name, str) or not check_name.strip():
            raise RuntimeStatusCodecError("Check name in 'checks' must be a non-empty string")
        if check_val not in ("ENABLED", "DISABLED"):
            raise RuntimeStatusCodecError(
                f"Check '{check_name}' value must be 'ENABLED' or 'DISABLED', got {check_val!r}"
            )
        checks[check_name] = check_val

    # 12. Validate worker_id
    worker_id = data["worker_id"]
    if not isinstance(worker_id, str):
        raise RuntimeStatusCodecError(f"Field 'worker_id' must be a string, got {type(worker_id).__name__}")
    if not WORKER_ID_REGEX.match(worker_id):
        raise RuntimeStatusCodecError(f"Field 'worker_id' '{worker_id}' does not match pattern {WORKER_ID_PATTERN}")

    # 13. Validate strict timezone datetimes
    observed_at = _parse_strict_iso_datetime(data["observed_at"], "observed_at")

    started_at: datetime | None = None
    if data.get("started_at") is not None:
        started_at = _parse_strict_iso_datetime(data["started_at"], "started_at")

    last_poll_at: datetime | None = None
    if data.get("last_poll_at") is not None:
        last_poll_at = _parse_strict_iso_datetime(data["last_poll_at"], "last_poll_at")

    # 14. Validate optional error
    raw_error = data.get("error")
    if raw_error is not None and not isinstance(raw_error, str):
        raise RuntimeStatusCodecError(f"Field 'error' must be a string or null, got {type(raw_error).__name__}")

    try:
        return RuntimeStatusDTO(
            schema_version="1.0",
            stream_id=stream_id,
            status=status,
            health=health,
            started_at=started_at,
            last_poll_at=last_poll_at,
            active_variant_count=raw_active_variants,
            queue_depth=raw_queue_depth,
            queue_lag_seconds=queue_lag_seconds,
            live_edge_lag_seconds=live_edge_lag_seconds,
            error=raw_error,
            telemetry_available=raw_telemetry,
            health_reasons=list(raw_reasons),
            checks=checks,
            worker_id=worker_id,
            observed_at=observed_at,
        )
    except ValidationError as exc:
        raise RuntimeStatusCodecError(
            "Runtime status payload violates DTO constraints"
        ) from exc
