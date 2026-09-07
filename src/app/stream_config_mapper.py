from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlparse

from models.stream_config import StreamConfig
from models.admission import LiveAdmissionPolicy, StartupAdmissionMode
from models.variant_selection import (
    VariantSelectionMode,
    VariantSelectionPolicy,
)


class StreamConfigMappingError(ValueError):
    pass


def stream_config_to_public(config: StreamConfig) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "stream_id": config.identity.external_stream_id,
        "master_url": config.master_url,
        "admission": {
            "startup_mode": config.admission_policy.startup_mode.value,
            "startup_lookback_segments": (
                config.admission_policy.startup_lookback_segments
            ),
            "soft_lag_target_durations": (
                config.admission_policy.soft_lag_target_durations
            ),
            "recovery_lag_target_durations": (
                config.admission_policy.recovery_lag_target_durations
            ),
            "hard_lag_target_durations": (
                config.admission_policy.hard_lag_target_durations
            ),
            "live_edge_retention_segments": (
                config.admission_policy.live_edge_retention_segments
            ),
            "transition_cycles": config.admission_policy.transition_cycles,
        },
        "variant_selection": {
            "mode": config.variant_selection.mode.value,
            "representative_count": (
                config.variant_selection.representative_count
            ),
            "explicit_variant_ids": list(
                config.variant_selection.explicit_variant_ids
            ),
        },
        "checks": {
            "black_screen": {
                "enabled": config.black_screen_enabled,
            },
            "video_freeze": {
                "enabled": config.video_freeze_enabled,
                "noise_db": float(config.freeze_noise_db),
                "detector_minimum_duration": float(
                    config.freeze_detector_minimum_duration
                ),
                "warning_duration_seconds": float(
                    config.freeze_warning_duration
                ),
                "alert_duration_seconds": float(
                    config.freeze_alert_duration
                ),
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
    allowed = {
        "schema_version",
        "stream_id",
        "master_url",
        "checks",
        "admission",
        "variant_selection",
    }
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
    admission = data.get(
        "admission",
        {
            "startup_mode": "bounded_history",
            "startup_lookback_segments": 4,
            "soft_lag_target_durations": 2.0,
            "recovery_lag_target_durations": 1.5,
            "hard_lag_target_durations": 6.0,
            "live_edge_retention_segments": 2,
            "transition_cycles": 3,
        },
    )
    variant_selection = data.get(
        "variant_selection",
        {
            "mode": "all",
            "representative_count": 3,
            "explicit_variant_ids": [],
        },
    )
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
    if not isinstance(admission, dict):
        raise StreamConfigMappingError("config.admission must be an object")
    if not isinstance(variant_selection, dict):
        raise StreamConfigMappingError(
            "config.variant_selection must be an object"
        )
    allowed_selection_fields = {
        "mode",
        "representative_count",
        "explicit_variant_ids",
    }
    if set(variant_selection) - allowed_selection_fields:
        raise StreamConfigMappingError("invalid config.variant_selection fields")
    try:
        selection_mode = VariantSelectionMode(
            variant_selection.get("mode", "all")
        )
    except (TypeError, ValueError) as exc:
        raise StreamConfigMappingError(
            "config.variant_selection.mode is invalid"
        ) from exc
    representative_count = variant_selection.get("representative_count", 3)
    if (
        isinstance(representative_count, bool)
        or not isinstance(representative_count, int)
        or representative_count <= 0
    ):
        raise StreamConfigMappingError(
            "config.variant_selection.representative_count must be > 0"
        )
    explicit_ids = variant_selection.get("explicit_variant_ids", [])
    if not isinstance(explicit_ids, list) or any(
        not isinstance(value, str) for value in explicit_ids
    ):
        raise StreamConfigMappingError(
            "config.variant_selection.explicit_variant_ids must be strings"
        )
    required_admission_fields = {
        "startup_mode",
        "startup_lookback_segments",
    }
    optional_admission_fields = {
        "soft_lag_target_durations",
        "recovery_lag_target_durations",
        "hard_lag_target_durations",
        "live_edge_retention_segments",
        "transition_cycles",
    }
    if (
        not required_admission_fields.issubset(admission)
        or set(admission)
        - required_admission_fields
        - optional_admission_fields
    ):
        raise StreamConfigMappingError("invalid config.admission fields")
    try:
        startup_mode = StartupAdmissionMode(admission["startup_mode"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StreamConfigMappingError(
            "config.admission.startup_mode is invalid"
        ) from exc
    startup_lookback = admission["startup_lookback_segments"]
    if (
        isinstance(startup_lookback, bool)
        or not isinstance(startup_lookback, int)
        or startup_lookback <= 0
    ):
        raise StreamConfigMappingError(
            "config.admission.startup_lookback_segments must be > 0"
        )
    soft_lag = _positive_finite_config_number(
        admission.get("soft_lag_target_durations", 2.0),
        "config.admission.soft_lag_target_durations",
    )
    recovery_lag = _positive_finite_config_number(
        admission.get("recovery_lag_target_durations", 1.5),
        "config.admission.recovery_lag_target_durations",
    )
    hard_lag = _positive_finite_config_number(
        admission.get("hard_lag_target_durations", 6.0),
        "config.admission.hard_lag_target_durations",
    )
    live_edge_retention = admission.get("live_edge_retention_segments", 2)
    if (
        isinstance(live_edge_retention, bool)
        or not isinstance(live_edge_retention, int)
        or live_edge_retention <= 0
    ):
        raise StreamConfigMappingError(
            "config.admission.live_edge_retention_segments must be > 0"
        )
    transition_cycles = admission.get("transition_cycles", 3)
    if (
        isinstance(transition_cycles, bool)
        or not isinstance(transition_cycles, int)
        or transition_cycles <= 0
    ):
        raise StreamConfigMappingError(
            "config.admission.transition_cycles must be > 0"
        )
    required_checks = {"black_screen", "audio_loss"}
    unknown_checks = set(checks) - {
        "black_screen",
        "audio_loss",
        "video_freeze",
    }
    missing_checks = required_checks - set(checks)
    if unknown_checks or missing_checks:
        raise StreamConfigMappingError(
            "invalid config.checks; "
            f"missing={sorted(missing_checks)}, "
            f"unknown={sorted(unknown_checks)}"
        )
    black = _check_object(checks, "black_screen", {"enabled"})
    audio = _check_object(
        checks,
        "audio_loss",
        {"enabled", "threshold_dbfs", "duration_seconds", "track_index"},
        optional={"track_index"},
    )
    freeze = checks.get("video_freeze")
    if freeze is None:
        freeze = {
            "enabled": False,
            "noise_db": -60.0,
            "detector_minimum_duration": 0.2,
            "warning_duration_seconds": 3.0,
            "alert_duration_seconds": 5.0,
        }
    elif isinstance(freeze, dict):
        freeze = _check_object(
            checks,
            "video_freeze",
            {
                "enabled",
                "noise_db",
                "detector_minimum_duration",
                "warning_duration_seconds",
                "alert_duration_seconds",
            },
        )
    else:
        raise StreamConfigMappingError("video_freeze must be an object")
    black_enabled = _boolean(black, "enabled", "black_screen")
    audio_enabled = _boolean(audio, "enabled", "audio_loss")
    freeze_enabled = _boolean(freeze, "enabled", "video_freeze")
    threshold = _finite_number(
        audio, "threshold_dbfs", check="audio_loss", maximum=0
    )
    duration = _finite_number(
        audio,
        "duration_seconds",
        check="audio_loss",
        exclusive_minimum=0,
    )
    freeze_noise = _finite_number(
        freeze, "noise_db", check="video_freeze", maximum=0
    )
    freeze_minimum = _finite_number(
        freeze,
        "detector_minimum_duration",
        check="video_freeze",
        exclusive_minimum=0,
    )
    freeze_warning = _finite_number(
        freeze,
        "warning_duration_seconds",
        check="video_freeze",
        exclusive_minimum=0,
    )
    freeze_alert = _finite_number(
        freeze,
        "alert_duration_seconds",
        check="video_freeze",
        exclusive_minimum=0,
    )
    track_index = audio.get("track_index", 0)
    if (
        isinstance(track_index, bool)
        or not isinstance(track_index, int)
        or track_index < 0
    ):
        raise StreamConfigMappingError("audio_loss.track_index must be >= 0")
    try:
        return StreamConfig(
            master_url=master_url.strip(),
            stream_id=stream_id.strip(),
            black_screen_enabled=black_enabled,
            video_freeze_enabled=freeze_enabled,
            freeze_noise_db=freeze_noise,
            freeze_detector_minimum_duration=freeze_minimum,
            freeze_warning_duration=freeze_warning,
            freeze_alert_duration=freeze_alert,
            audio_loss_enabled=audio_enabled,
            silence_threshold_dbfs=threshold,
            audio_loss_duration=duration,
            audio_track_index=track_index,
            admission_policy=LiveAdmissionPolicy(
                startup_mode=startup_mode,
                startup_lookback_segments=startup_lookback,
                soft_lag_target_durations=soft_lag,
                recovery_lag_target_durations=recovery_lag,
                hard_lag_target_durations=hard_lag,
                live_edge_retention_segments=live_edge_retention,
                transition_cycles=transition_cycles,
            ),
            variant_selection=VariantSelectionPolicy(
                mode=selection_mode,
                representative_count=representative_count,
                explicit_variant_ids=tuple(explicit_ids),
            ),
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
            f"invalid {name} fields; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
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
    check: str,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StreamConfigMappingError(f"{check}.{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise StreamConfigMappingError(f"{check}.{field} must be finite")
    if maximum is not None and result > maximum:
        raise StreamConfigMappingError(
            f"{check}.{field} must be <= {maximum}"
        )
    if exclusive_minimum is not None and result <= exclusive_minimum:
        raise StreamConfigMappingError(
            f"{check}.{field} must be > {exclusive_minimum}"
        )
    return result


def _positive_finite_config_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StreamConfigMappingError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise StreamConfigMappingError(f"{field} must be finite and > 0")
    return result
