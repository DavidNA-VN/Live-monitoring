from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Dict, Optional

from ..models import (
    CommandResultDTO,
    CommandResultStatusEnum,
    CommandSubmissionDTO,
    CommandTypeEnum,
    StreamConfigDTO,
)


class CommandCodecError(ValueError):
    """Lỗi khi serialize hoặc parse dữ liệu command."""
    pass


def _utc_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandCodecError(
                f"{field} must be an ISO-8601 datetime"
            ) from exc
    else:
        raise CommandCodecError(f"{field} must be an ISO-8601 datetime")
    if parsed.tzinfo is None:
        raise CommandCodecError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def build_command_payload(
    *,
    command_id: str,
    command_type: CommandTypeEnum,
    stream_id: str,
    requested_at: datetime,
    config: Optional[StreamConfigDTO] = None,
) -> Dict[str, Any]:
    """Tạo payload dict tuân thủ monitoring-command.schema.json."""
    requested_at = _utc_datetime(requested_at, "requested_at")

    payload: Dict[str, Any] = {
        "schema_version": "1.0",
        "command_id": command_id,
        "command_type": command_type.value,
        "stream_id": stream_id,
        "requested_at": requested_at.isoformat(),
    }
    if config is not None:
        payload["config"] = config.model_dump(mode="json")
    return payload


def compute_logical_fingerprint(
    *,
    command_type: CommandTypeEnum,
    stream_id: str,
    config: Optional[StreamConfigDTO] = None,
) -> str:
    """
    Tính fingerprint dựa trên logical request parameters
    (command_type, stream_id, config) độc lập với requested_at và command_id.
    """
    logical: Dict[str, Any] = {
        "command_type": command_type.value,
        "stream_id": stream_id,
    }
    if config is not None:
        logical["config"] = config.model_dump(mode="json")
    canonical = json.dumps(logical, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_submission_record(
    *,
    command_id: str,
    stream_id: str,
    fingerprint: str,
    requested_at: datetime,
) -> Dict[str, Any]:
    """Tạo record lưu vào submitted_command / idempotency_key marker."""
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    else:
        requested_at = requested_at.astimezone(timezone.utc)

    return {
        "schema_version": "1.0",
        "command_id": command_id,
        "stream_id": stream_id,
        "status": "ACCEPTED",
        "fingerprint": fingerprint,
        "submitted_at": requested_at.isoformat(),
    }


def parse_submission_record(data: Dict[str, Any]) -> CommandSubmissionDTO:
    """Parse submission record thành CommandSubmissionDTO."""
    try:
        required = {
            "schema_version",
            "command_id",
            "stream_id",
            "status",
            "fingerprint",
            "submitted_at",
        }
        if set(data) != required:
            raise CommandCodecError("submission record has invalid fields")
        if data["schema_version"] != "1.0" or data["status"] != "ACCEPTED":
            raise CommandCodecError(
                "submission record has invalid version or status"
            )
        if (
            not isinstance(data["fingerprint"], str)
            or len(data["fingerprint"]) != 64
        ):
            raise CommandCodecError("submission record has invalid fingerprint")
        _utc_datetime(data["submitted_at"], "submitted_at")
        return CommandSubmissionDTO(
            schema_version="1.0",
            command_id=data["command_id"],
            stream_id=data["stream_id"],
            status="ACCEPTED",
            message=f"Command accepted for stream {data['stream_id']}",
        )
    except Exception as exc:
        raise CommandCodecError(f"Invalid submission record: {exc}") from exc


def parse_command_result(data: Dict[str, Any]) -> CommandResultDTO:
    """Parse result record từ Redis marker thành CommandResultDTO."""
    try:
        required = {
            "schema_version",
            "command_id",
            "command_type",
            "stream_id",
            "status",
            "changed",
            "processed_at",
            "error_code",
            "error",
        }
        if set(data) != required:
            raise CommandCodecError("command result has invalid fields")
        if data["schema_version"] != "1.0":
            raise CommandCodecError(
                "command result has unsupported schema_version"
            )
        if type(data["changed"]) is not bool:
            raise CommandCodecError("changed must be a boolean")
        processed_at = _utc_datetime(data["processed_at"], "processed_at")

        return CommandResultDTO(
            schema_version="1.0",
            command_id=data["command_id"],
            command_type=CommandTypeEnum(data["command_type"]),
            stream_id=data["stream_id"],
            status=CommandResultStatusEnum(data["status"]),
            changed=data["changed"],
            processed_at=processed_at,
            error_code=data.get("error_code"),
            error=data.get("error"),
        )
    except Exception as exc:
        raise CommandCodecError(f"Invalid command result payload: {exc}") from exc
