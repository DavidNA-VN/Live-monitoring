import json
from pathlib import Path
import re

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"
WORKER_ID_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as schema_file:
        return json.load(schema_file)


def sample_metrics_payload() -> dict:
    return {
        "schema_version": "1.0",
        "worker_id": "worker-01",
        "observed_at": "2026-08-27T10:00:00Z",
        "command_delivery_received_total": 120,
        "command_applied_total": 80,
        "command_noop_total": 12,
        "command_rejected_total": 20,
        "command_failed_total": 8,
        "command_reclaimed_total": 3,
        "command_duplicate_replay_total": 15,
        "command_dead_letter_total": 6,
        "command_oversized_total": 2,
        "command_stale_total": 1,
        "command_poll_failure_total": 0,
        "command_processing_duration_ms_total": 1540.5,
        "command_pending_count": 2,
        "command_deferred_count": 0,
        "command_processing_duration_ms_max": 45.2,
        "last_command_processing_duration_ms": 12.3,
        "last_successful_poll_at": "2026-08-27T09:59:59Z",
        "last_command_processed_at": "2026-08-27T10:00:00Z",
        "last_error_code": None,
    }


def test_worker_command_metrics_schema_structure():
    schema = load_schema("worker-command-metrics.schema.json")
    assert schema.get("$schema", "").endswith("2020-12/schema")
    assert schema.get("type") == "object"
    assert schema.get("additionalProperties") is False
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert "worker_id" in schema["required"]
    assert "observed_at" in schema["required"]
    assert schema["properties"]["worker_id"]["pattern"] == "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"

    required_counters = [
        "command_delivery_received_total",
        "command_applied_total",
        "command_noop_total",
        "command_rejected_total",
        "command_failed_total",
        "command_reclaimed_total",
        "command_duplicate_replay_total",
        "command_dead_letter_total",
        "command_oversized_total",
        "command_stale_total",
        "command_poll_failure_total",
        "command_processing_duration_ms_total",
    ]
    for counter in required_counters:
        assert counter in schema["required"]
        assert counter in schema["properties"]
        assert schema["properties"][counter]["minimum"] == 0

    assert "command_pending_count" in schema["required"]
    assert "command_deferred_count" in schema["required"]
    assert "command_processing_duration_ms_max" in schema["required"]
    assert "last_command_processing_duration_ms" in schema["required"]


def test_worker_command_metrics_sample_payload_matches_schema():
    schema = load_schema("worker-command-metrics.schema.json")
    payload = sample_metrics_payload()

    # All required fields present
    for req in schema["required"]:
        assert req in payload

    # No unallowed additional fields
    assert set(payload.keys()).issubset(set(schema["properties"].keys()))

    # Worker ID pattern
    assert WORKER_ID_REGEX.match(payload["worker_id"])

    # Non-negative counters
    for key, value in payload.items():
        if key.endswith("_total") or key.endswith("_count"):
            assert isinstance(value, (int, float))
            assert value >= 0
