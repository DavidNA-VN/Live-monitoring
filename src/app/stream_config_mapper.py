from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlparse

from models.stream_config import StreamConfig


class StreamConfigMappingError(ValueError):
    pass


def stream_config_to_public(config: StreamConfig) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "stream_id": config.identity.external_stream_id,
        "master_url": config.master_url,
        "checks": {
            "black_screen": {
                "enabled": config.black_screen_enabled,
            },
            "audio_loss": {
                "enabled": config.audio_loss_enabled,
                "threshold_dbfs": float(config.silence_threshold_dbfs),
                "duration_seconds": float(config.audio_loss_duration),
                "track_index": int(config.audio_track_index),
            },
        },
    }


def stream_config_from_public(data: object) -> StreamConfig:
    if not isinstance(data, dict):
        raise StreamConfigMappingError("config must be an object")
    allowed = {"schema_version", "stream_id", "master_url", "checks"}
    unknown = set(data) - allowed
    if unknown:
        raise StreamConfigMappingError(
            f"unknown config fields: {', '.join(sorted(unknown))}"
        )
    if data.get("schema_version") != "1.0":
        raise StreamConfigMappingError("unsupported config schema_version")
    stream_id = data.get("stream_id")
    master_url = data.get("master_url")
    checks = data.get("checks")
    if not isinstance(stream_id, str) or not stream_id.strip():
        raise StreamConfigMappingError("config.stream_id must not be empty")
    if not isinstance(master_url, str) or not master_url.strip():
        raise StreamConfigMappingError("config.master_url must not be empty")
    parsed_url = urlparse(master_url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise StreamConfigMappingError(
            "config.master_url must be an absolute HTTP(S) URL"
        )
    if not isinstance(checks, dict):
        raise StreamConfigMappingError("config.checks must be an object")
    if set(checks) != {"black_screen", "audio_loss"}:
        raise StreamConfigMappingError(
            "config.checks must contain only black_screen and audio_loss"
        )
    black = _check_object(checks, "black_screen", {"enabled"})
    audio = _check_object(
        checks,
        "audio_loss",
        {"enabled", "threshold_dbfs", "duration_seconds", "track_index"},
        optional={"track_index"},
    )
    black_enabled = _boolean(black, "enabled", "black_screen")
    audio_enabled = _boolean(audio, "enabled", "audio_loss")
    threshold = _finite_number(audio, "threshold_dbfs", maximum=0)
    duration = _finite_number(audio, "duration_seconds", exclusive_minimum=0)
    track_index = audio.get("track_index", 0)
    if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
        raise StreamConfigMappingError("audio_loss.track_index must be >= 0")
    try:
        return StreamConfig(
            master_url=master_url.strip(),
            stream_id=stream_id.strip(),
            black_screen_enabled=black_enabled,
            audio_loss_enabled=audio_enabled,
            silence_threshold_dbfs=threshold,
            audio_loss_duration=duration,
            audio_track_index=track_index,
        )
    except (TypeError, ValueError) as exc:
        raise StreamConfigMappingError(str(exc)) from exc


def _check_object(
    checks: dict[str, Any],
    name: str,
    allowed: set[str],
    *,
    optional: set[str] | None = None,
) -> dict[str, Any]:
    value = checks.get(name)
    if not isinstance(value, dict):
        raise StreamConfigMappingError(f"{name} must be an object")
    unknown = set(value) - allowed
    required = allowed - (optional or set())
    missing = required - set(value)
    if unknown or missing:
        raise StreamConfigMappingError(
            f"invalid {name} fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    return value


def _boolean(data: dict[str, Any], field: str, check: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise StreamConfigMappingError(f"{check}.{field} must be boolean")
    return value


def _finite_number(
    data: dict[str, Any],
    field: str,
    *,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StreamConfigMappingError(f"audio_loss.{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise StreamConfigMappingError(f"audio_loss.{field} must be finite")
    if maximum is not None and result > maximum:
        raise StreamConfigMappingError(f"audio_loss.{field} must be <= {maximum}")
    if exclusive_minimum is not None and result <= exclusive_minimum:
        raise StreamConfigMappingError(
            f"audio_loss.{field} must be > {exclusive_minimum}"
        )
    return result
