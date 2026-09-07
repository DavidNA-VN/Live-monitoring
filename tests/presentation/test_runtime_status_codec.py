from datetime import datetime, timezone
import json
import pytest

from presentation.api.adapters.runtime_status_codec import (
    RuntimeStatusCodecError,
    parse_public_runtime_status,
)
from presentation.api.models import StreamHealthEnum, StreamStatusEnum


def valid_status_payload() -> dict:
    return {
        "schema_version": "1.0",
        "stream_id": "chan-01",
        "status": "RUNNING",
        "health": "HEALTHY",
        "started_at": "2026-08-28T09:00:00+00:00",
        "last_poll_at": "2026-08-28T10:00:00+00:00",
        "active_variant_count": 3,
        "queue_depth": 5,
        "queue_lag_seconds": 1.25,
        "live_edge_lag_seconds": 2.5,
        "error": None,
        "telemetry_available": True,
        "health_reasons": ["All variants healthy"],
        "checks": {
            "black_screen": "ENABLED",
            "audio_loss": "DISABLED",
        },
        "worker_id": "worker-node-01",
        "observed_at": "2026-08-28T10:00:05+00:00",
        "admission_mode": "catch_up",
        "startup_segments_outside_scope": 2,
        "dropped_expired_work": 1,
        "dropped_capacity_work": 3,
        "dropped_live_edge_work": 4,
        "coverage_gap_count": 2,
        "coverage_gap_segment_count": 5,
        "dropped_media_segment_count": 3,
        "profile_analysis_total": {"video_realtime": 12},
        "active_media_processes": 2,
        "max_media_processes": 4,
    }


def test_1_parse_full_valid_snapshot():
    payload = valid_status_payload()
    dto = parse_public_runtime_status(payload, requested_stream_id="chan-01")

    assert dto.schema_version == "1.0"
    assert dto.stream_id == "chan-01"
    assert dto.status == StreamStatusEnum.RUNNING
    assert dto.health == StreamHealthEnum.HEALTHY
    assert dto.active_variant_count == 3
    assert dto.queue_depth == 5
    assert dto.queue_lag_seconds == 1.25
    assert dto.live_edge_lag_seconds == 2.5
    assert dto.telemetry_available is True
    assert dto.worker_id == "worker-node-01"
    assert dto.observed_at == datetime(2026, 8, 28, 10, 0, 5, tzinfo=timezone.utc)
    assert dto.started_at == datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)
    assert dto.last_poll_at == datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
    assert dto.checks == {"black_screen": "ENABLED", "audio_loss": "DISABLED"}
    assert dto.health_reasons == ["All variants healthy"]
    assert dto.admission_mode == "catch_up"
    assert dto.coverage_gap_count == 2
    assert dto.dropped_media_segment_count == 3
    assert dto.profile_analysis_total == {"video_realtime": 12}
    assert dto.active_media_processes == 2
    assert dto.max_media_processes == 4


def test_2_parse_optional_nullable_fields():
    payload = valid_status_payload()
    payload["started_at"] = None
    payload["last_poll_at"] = None
    payload["queue_lag_seconds"] = None
    payload["live_edge_lag_seconds"] = None
    payload["error"] = None
    payload["health_reasons"] = []

    dto = parse_public_runtime_status(payload, requested_stream_id="chan-01")
    assert dto.started_at is None
    assert dto.last_poll_at is None
    assert dto.queue_lag_seconds is None
    assert dto.live_edge_lag_seconds is None
    assert dto.error is None
    assert dto.health_reasons == []


def test_3_accept_bytes_and_string_values():
    payload = valid_status_payload()
    raw_str = json.dumps(payload)
    raw_bytes = raw_str.encode("utf-8")

    dto_str = parse_public_runtime_status(raw_str, requested_stream_id="chan-01")
    dto_bytes = parse_public_runtime_status(raw_bytes, requested_stream_id="chan-01")

    assert dto_str.stream_id == "chan-01"
    assert dto_bytes.stream_id == "chan-01"


def test_4_reject_invalid_json_syntax():
    with pytest.raises(RuntimeStatusCodecError, match="Invalid JSON syntax"):
        parse_public_runtime_status("{invalid-json-string", requested_stream_id="chan-01")


def test_5_reject_non_object_json():
    with pytest.raises(RuntimeStatusCodecError, match="must be a JSON object"):
        parse_public_runtime_status("[\"list\", \"not\", \"object\"]", requested_stream_id="chan-01")


def test_6_reject_missing_required_fields():
    required_fields = ["worker_id", "observed_at", "checks", "active_variant_count", "queue_depth", "health", "status"]
    for field in required_fields:
        payload = valid_status_payload()
        del payload[field]
        with pytest.raises(RuntimeStatusCodecError, match="Missing required fields"):
            parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_7_reject_unknown_extra_fields():
    payload = valid_status_payload()
    payload["unexpected_field"] = "malicious_data"
    with pytest.raises(RuntimeStatusCodecError, match="Unexpected extra fields"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_8_reject_storage_id():
    payload = valid_status_payload()
    payload["storage_id"] = "session-internal-uuid"
    with pytest.raises(RuntimeStatusCodecError, match="Unexpected extra fields.*storage_id"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_9_reject_wrong_schema_version():
    payload = valid_status_payload()
    payload["schema_version"] = "2.0"
    with pytest.raises(RuntimeStatusCodecError, match="Unsupported schema_version"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_10_reject_active_variant_count_boolean():
    payload = valid_status_payload()
    payload["active_variant_count"] = True  # In Python, isinstance(True, int) is True, must be rejected
    with pytest.raises(RuntimeStatusCodecError, match="Field 'active_variant_count' must be an integer"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_11_reject_queue_depth_boolean():
    payload = valid_status_payload()
    payload["queue_depth"] = False
    with pytest.raises(RuntimeStatusCodecError, match="Field 'queue_depth' must be an integer"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_12_reject_negative_counters():
    payload1 = valid_status_payload()
    payload1["active_variant_count"] = -1
    with pytest.raises(RuntimeStatusCodecError, match="cannot be negative"):
        parse_public_runtime_status(payload1, requested_stream_id="chan-01")

    payload2 = valid_status_payload()
    payload2["queue_depth"] = -5
    with pytest.raises(RuntimeStatusCodecError, match="cannot be negative"):
        parse_public_runtime_status(payload2, requested_stream_id="chan-01")


def test_13_reject_non_finite_queue_lag():
    for non_finite in [float("inf"), float("-inf"), float("nan")]:
        payload = valid_status_payload()
        payload["queue_lag_seconds"] = non_finite
        with pytest.raises(RuntimeStatusCodecError, match="must be finite"):
            parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_reject_invalid_live_edge_lag():
    for invalid in [True, -1.0, float("inf"), float("nan")]:
        payload = valid_status_payload()
        payload["live_edge_lag_seconds"] = invalid
        with pytest.raises(RuntimeStatusCodecError):
            parse_public_runtime_status(
                payload,
                requested_stream_id="chan-01",
            )


def test_14_reject_invalid_check_status():
    payload = valid_status_payload()
    payload["checks"]["black_screen"] = "ACTIVE"
    with pytest.raises(RuntimeStatusCodecError, match="value must be 'ENABLED' or 'DISABLED'"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_15_reject_malformed_worker_id():
    payload = valid_status_payload()
    payload["worker_id"] = "invalid worker with spaces"
    with pytest.raises(RuntimeStatusCodecError, match="does not match pattern"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_16_reject_observed_at_without_timezone():
    payload = valid_status_payload()
    payload["observed_at"] = "2026-08-28T10:00:05"  # Naive datetime
    with pytest.raises(RuntimeStatusCodecError, match="must include timezone offset"):
        parse_public_runtime_status(payload, requested_stream_id="chan-01")


def test_17_reject_payload_stream_id_mismatch():
    payload = valid_status_payload()
    payload["stream_id"] = "chan-01"
    with pytest.raises(RuntimeStatusCodecError, match="Mismatched stream_id"):
        parse_public_runtime_status(payload, requested_stream_id="chan-other-99")


def test_18_wraps_remaining_dto_constraint_failures():
    payload = valid_status_payload()
    payload["stream_id"] = "x" * 129
    with pytest.raises(RuntimeStatusCodecError, match="DTO constraints"):
        parse_public_runtime_status(payload, requested_stream_id="x" * 129)
