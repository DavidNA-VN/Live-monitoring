from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from app.stream_config_mapper import (
    stream_config_from_public,
    stream_config_to_public,
)
from models.desired_stream_state import (
    DESIRED_STATE_SCHEMA_VERSION,
    DesiredLifecycleState,
    DesiredStreamState,
)


_FIELDS = {
    "schema_version",
    "stream_id",
    "desired_state",
    "updated_at",
    "config",
    "last_command_id",
}


def desired_state_to_dict(record: DesiredStreamState) -> dict[str, Any]:
    return {
        "schema_version": record.schema_version,
        "stream_id": record.stream_id,
        "desired_state": record.desired_state.value,
        "updated_at": record.updated_at.astimezone(timezone.utc).isoformat(),
        "config": (
            stream_config_to_public(record.config)
            if record.config is not None
            else None
        ),
        "last_command_id": record.last_command_id,
    }


def desired_state_from_dict(data: object) -> DesiredStreamState:
    if not isinstance(data, dict):
        raise ValueError("desired state payload must be an object")
    unknown = set(data) - _FIELDS
    missing = _FIELDS - set(data)
    if unknown or missing:
        raise ValueError(
            f"invalid desired state fields; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    if data.get("schema_version") != DESIRED_STATE_SCHEMA_VERSION:
        raise ValueError("unsupported desired state schema_version")

    raw_updated_at = data.get("updated_at")
    if not isinstance(raw_updated_at, str):
        raise ValueError("updated_at must be an ISO-8601 string")
    try:
        updated_at = datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("updated_at must be an ISO-8601 datetime") from exc
    if updated_at.tzinfo is None:
        raise ValueError("updated_at must include a timezone")

    try:
        desired_state = DesiredLifecycleState(data.get("desired_state"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid desired_state") from exc

    raw_config = data.get("config")
    config = (
        stream_config_from_public(raw_config)
        if raw_config is not None
        else None
    )
    return DesiredStreamState(
        schema_version=DESIRED_STATE_SCHEMA_VERSION,
        stream_id=data.get("stream_id"),
        desired_state=desired_state,
        updated_at=updated_at.astimezone(timezone.utc),
        config=config,
        last_command_id=data.get("last_command_id"),
    )


def desired_state_from_json(payload: str) -> DesiredStreamState:
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("desired state payload must be valid JSON") from exc
    return desired_state_from_dict(data)
