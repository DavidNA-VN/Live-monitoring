from collections.abc import Mapping
from datetime import datetime
import json
from typing import Any, Dict, Optional, Set

from pydantic import ValidationError

from ..models import (
    AlertCategory,
    AlertDTO,
    AlertState,
    EventType,
)

KNOWN_FIELDS: Set[str] = {
    "schema_version",
    "alert_id",
    "event_id",
    "category",
    "type",
    "state",
    "stream_id",
    "occurred_at",
    "emitted_at",
    "reason",
    "payload",
    "check",
    "variant_id",
    "variant_stable_id",
    "event_started_at",
    "event_ended_at",
}

REQUIRED_FIELDS: Set[str] = {
    "schema_version",
    "alert_id",
    "event_id",
    "category",
    "type",
    "state",
    "stream_id",
    "occurred_at",
    "emitted_at",
    "reason",
    "payload",
}


class AlertCodecError(ValueError):
    """Lỗi khi parse hoặc validate alert transport fields từ Redis outbox."""
    pass


def _decode_utf8(val: bytes | str, field_name: str) -> str:
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AlertCodecError(f"Field '{field_name}' contains invalid UTF-8 bytes: {exc}") from exc
    elif isinstance(val, str):
        return val
    raise AlertCodecError(f"Field '{field_name}' must be bytes or str, got {type(val).__name__}")


def _parse_strict_iso_datetime(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AlertCodecError(f"Field '{field_name}' must be a non-empty ISO-8601 string")
    try:
        dt = datetime.fromisoformat(value)
    except Exception as exc:
        raise AlertCodecError(f"Field '{field_name}' has invalid ISO-8601 datetime format: {exc}") from exc

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AlertCodecError(f"Field '{field_name}' must include timezone offset, naive datetime is rejected")
    return dt


def parse_alert_fields(
    fields: Mapping[bytes | str, bytes | str],
    *,
    requested_stream_id: Optional[str] = None,
) -> AlertDTO:
    """
    Parse và validate flat Redis outbox stream entry fields thành AlertDTO.
    Tương thích với flattened attributes của producer và loại bỏ poison entries.
    """
    if not isinstance(fields, Mapping):
        raise AlertCodecError(f"Expected Mapping for alert fields, got {type(fields).__name__}")

    # 1. Chuẩn hóa toàn bộ key/value sang UTF-8 str
    normalized: Dict[str, str] = {}
    for raw_k, raw_v in fields.items():
        k_str = _decode_utf8(raw_k, "key")
        v_str = _decode_utf8(raw_v, k_str)
        normalized[k_str] = v_str

    # 2. Cấm tuyệt đối trường storage_id
    if "storage_id" in normalized:
        raise AlertCodecError("Field 'storage_id' is forbidden in public alert transport")

    # 3. Kiểm tra các trường bắt buộc
    missing = REQUIRED_FIELDS - set(normalized.keys())
    if missing:
        raise AlertCodecError(f"Alert missing required fields: {sorted(missing)}")

    # 4. Parse và validate JSON payload (attributes)
    raw_payload = normalized["payload"]
    try:
        attributes = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise AlertCodecError(f"Alert payload is not valid JSON: {exc}") from exc

    if not isinstance(attributes, dict):
        raise AlertCodecError(f"Alert payload must be a JSON object, got {type(attributes).__name__}")

    for attr_k, attr_v in attributes.items():
        if not isinstance(attr_k, str) or not isinstance(attr_v, str):
            raise AlertCodecError(
                f"All attribute keys and values in payload must be strings, got ({type(attr_k).__name__}: {type(attr_v).__name__})"
            )
        if attr_k == "storage_id":
            raise AlertCodecError("Field 'storage_id' in payload attributes is forbidden")
        if attr_k in KNOWN_FIELDS:
            raise AlertCodecError(
                f"Payload attribute '{attr_k}' conflicts with a canonical alert field"
            )

    # 5. Đối chiếu flattened attributes với JSON payload
    unknown_fields = set(normalized.keys()) - KNOWN_FIELDS
    for field_name in unknown_fields:
        if field_name not in attributes:
            raise AlertCodecError(
                f"Unexpected extra field '{field_name}' not recognized and not in payload attributes"
            )
        if normalized[field_name] != attributes[field_name]:
            raise AlertCodecError(
                f"Flattened attribute '{field_name}' value '{normalized[field_name]}' does not match payload value '{attributes[field_name]}'"
            )

    # 6. Validate schema_version
    schema_version = normalized["schema_version"]
    if schema_version != "1.0":
        raise AlertCodecError(f"Unsupported alert schema: {schema_version!r}")

    # 7. Validate non-empty string IDs và reason
    alert_id = normalized["alert_id"]
    if not alert_id.strip():
        raise AlertCodecError("Field 'alert_id' must be a non-empty string")

    event_id = normalized["event_id"]
    if not event_id.strip():
        raise AlertCodecError("Field 'event_id' must be a non-empty string")

    reason = normalized["reason"]
    if not reason.strip():
        raise AlertCodecError("Field 'reason' must be a non-empty string")

    # 8. Validate stream_id và đối chiếu requested_stream_id
    stream_id = normalized["stream_id"]
    if not stream_id.strip() or len(stream_id) > 128:
        raise AlertCodecError(f"Field 'stream_id' must be 1..128 chars, got length {len(stream_id)}")

    if requested_stream_id is not None and stream_id != requested_stream_id:
        raise AlertCodecError(
            f"Mismatched stream_id: payload contains '{stream_id}' but query requested '{requested_stream_id}'"
        )

    # 9. Validate enums
    raw_category = normalized["category"]
    try:
        category = AlertCategory(raw_category)
    except ValueError as exc:
        raise AlertCodecError(f"Invalid alert category '{raw_category}': {exc}") from exc

    raw_type = normalized["type"]
    try:
        event_type = EventType(raw_type)
    except ValueError as exc:
        raise AlertCodecError(f"Invalid alert event_type '{raw_type}': {exc}") from exc

    raw_state = normalized["state"]
    try:
        state = AlertState(raw_state)
    except ValueError as exc:
        raise AlertCodecError(f"Invalid alert state '{raw_state}': {exc}") from exc

    # 10. Validate datetimes có timezone
    occurred_at = _parse_strict_iso_datetime(normalized["occurred_at"], "occurred_at")
    emitted_at = _parse_strict_iso_datetime(normalized["emitted_at"], "emitted_at")

    event_started_at: Optional[datetime] = None
    if "event_started_at" in normalized and normalized["event_started_at"].strip():
        event_started_at = _parse_strict_iso_datetime(normalized["event_started_at"], "event_started_at")

    event_ended_at: Optional[datetime] = None
    if "event_ended_at" in normalized and normalized["event_ended_at"].strip():
        event_ended_at = _parse_strict_iso_datetime(normalized["event_ended_at"], "event_ended_at")

    # 11. Optional fields
    check = normalized.get("check")
    variant_id = normalized.get("variant_id")
    variant_stable_id = normalized.get("variant_stable_id")

    try:
        return AlertDTO(
            schema_version="1.0",
            alert_id=alert_id,
            event_id=event_id,
            category=category,
            event_type=event_type,
            state=state,
            stream_id=stream_id,
            check=check,
            variant_id=variant_id,
            variant_stable_id=variant_stable_id,
            occurred_at=occurred_at,
            emitted_at=emitted_at,
            event_started_at=event_started_at,
            event_ended_at=event_ended_at,
            reason=reason,
            attributes=attributes,
        )
    except ValidationError as exc:
        raise AlertCodecError(f"AlertDTO validation failed: {exc}") from exc
