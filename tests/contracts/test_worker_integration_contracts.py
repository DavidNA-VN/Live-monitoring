import json
from pathlib import Path
import re

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"
WORKER_ID_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as schema_file:
        return json.load(schema_file)


def test_integration_schemas_parse_as_valid_json():
    schemas = [
        "runtime-status.schema.json",
        "runtime-status-update.schema.json",
        "worker-heartbeat.schema.json",
    ]
    for name in schemas:
        schema = load_schema(name)
        assert isinstance(schema, dict)
        assert schema.get("$schema", "").endswith("2020-12/schema")
        assert schema.get("type") == "object"
        assert schema.get("additionalProperties") is False


def test_integration_schemas_use_schema_version_1_0():
    schemas = [
        "runtime-status.schema.json",
        "runtime-status-update.schema.json",
        "worker-heartbeat.schema.json",
    ]
    for name in schemas:
        schema = load_schema(name)
        assert schema["properties"]["schema_version"]["const"] == "1.0"
        assert "schema_version" in schema["required"]


def test_worker_id_present_in_heartbeat_and_update_schemas():
    update_schema = load_schema("runtime-status-update.schema.json")
    heartbeat_schema = load_schema("worker-heartbeat.schema.json")

    assert "worker_id" in update_schema["required"]
    assert "worker_id" in update_schema["properties"]
    assert update_schema["properties"]["worker_id"]["pattern"] == (
        "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )

    assert "worker_id" in heartbeat_schema["required"]
    assert "worker_id" in heartbeat_schema["properties"]
    assert heartbeat_schema["properties"]["worker_id"]["pattern"] == (
        "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )


def test_runtime_status_has_optional_worker_id_and_observed_at_backward_compatible():
    status_schema = load_schema("runtime-status.schema.json")
    props = status_schema["properties"]

    # Properties exist
    assert "worker_id" in props
    assert props["worker_id"]["pattern"] == "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    assert "observed_at" in props
    assert props["observed_at"]["format"] == "date-time"

    # Backward compatibility: must NOT be in required list
    required = set(status_schema["required"])
    assert "worker_id" not in required
    assert "observed_at" not in required
    assert required == {
        "schema_version",
        "stream_id",
        "status",
        "health",
        "active_variant_count",
        "queue_depth",
        "checks",
    }


def test_heartbeat_state_enum_values():
    schema = load_schema("worker-heartbeat.schema.json")
    state_enum = schema["properties"]["state"]["enum"]
    assert state_enum == ["STARTING", "READY", "DEGRADED", "STOPPING"]


def test_status_update_type_enum_values():
    schema = load_schema("runtime-status-update.schema.json")
    update_type_enum = schema["properties"]["update_type"]["enum"]
    assert update_type_enum == ["SNAPSHOT", "REMOVED"]


def test_snapshot_requires_status_and_removed_forbids_status():
    schema = load_schema("runtime-status-update.schema.json")
    assert "allOf" in schema

    conditional_rules = schema["allOf"]
    snapshot_rule = next(
        (
            rule for rule in conditional_rules
            if rule.get("if", {}).get("properties", {}).get("update_type", {}).get("const") == "SNAPSHOT"
            or "SNAPSHOT" in rule.get("if", {}).get("properties", {}).get("update_type", {}).get("enum", [])
        ),
        None,
    )
    assert snapshot_rule is not None
    assert "status" in snapshot_rule["then"]["required"]
    assert snapshot_rule["else"] == {
        "not": {"required": ["status"]}
    }


def test_projected_snapshot_status_requires_worker_and_observation_fields():
    schema = load_schema("runtime-status-update.schema.json")
    status_rules = schema["properties"]["status"]["allOf"]

    assert {"$ref": "runtime-status.schema.json"} in status_rules
    projection_rule = next(rule for rule in status_rules if "required" in rule)
    assert projection_rule["required"] == ["worker_id", "observed_at"]


def test_public_schemas_do_not_contain_storage_id_or_internal_tokens():
    all_schemas = [
        "stream-config.schema.json",
        "monitoring-command.schema.json",
        "monitoring-command-result.schema.json",
        "runtime-status.schema.json",
        "runtime-status-update.schema.json",
        "worker-heartbeat.schema.json",
        "alert.schema.json",
    ]

    forbidden_keywords = [
        "storage_id",
        "ownership_token",
        "lease_token",
        "fencing_generation",
        "fencing_token",
    ]

    for schema_name in all_schemas:
        schema = load_schema(schema_name)
        schema_str = json.dumps(schema)
        for keyword in forbidden_keywords:
            assert keyword not in schema_str, (
                f"Found forbidden internal keyword '{keyword}' "
                f"in '{schema_name}'"
            )


def test_worker_id_pattern_and_length_validation():
    valid_ids = [
        "worker-local-01",
        "monitor-node-a",
        "worker.prod.001",
        "worker_01",
        "A",
        "w" * 128,
    ]
    for valid_id in valid_ids:
        assert WORKER_ID_REGEX.match(valid_id) is not None

    invalid_ids = [
        "",                     # empty
        "-worker",              # starts with hyphen
        ".worker",              # starts with dot
        "_worker",              # starts with underscore
        "worker@node",          # contains invalid character '@'
        "worker 01",            # contains whitespace
        "worker$01",            # contains special char '$'
        "w" * 129,              # length > 128
    ]
    for invalid_id in invalid_ids:
        assert WORKER_ID_REGEX.match(invalid_id) is None


def test_capacity_and_count_fields_constraints():
    schema = load_schema("worker-heartbeat.schema.json")
    props = schema["properties"]

    assert props["active_stream_count"]["type"] == "integer"
    assert props["active_stream_count"]["minimum"] == 0

    assert props["max_streams"]["type"] == "integer"
    assert props["max_streams"]["minimum"] == 1

    assert props["active_media_processes"]["type"] == "integer"
    assert props["active_media_processes"]["minimum"] == 0

    assert props["max_media_processes"]["type"] == "integer"
    assert props["max_media_processes"]["minimum"] == 1


def test_snapshot_sample_payload_consistency():
    sample_snapshot = {
        "schema_version": "1.0",
        "update_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "update_type": "SNAPSHOT",
        "stream_id": "channel-01",
        "worker_id": "worker-local-01",
        "observed_at": "2026-08-27T10:00:00+00:00",
        "status": {
            "schema_version": "1.0",
            "stream_id": "channel-01",
            "status": "RUNNING",
            "health": "HEALTHY",
            "active_variant_count": 2,
            "queue_depth": 0,
            "telemetry_available": True,
            "worker_id": "worker-local-01",
            "observed_at": "2026-08-27T10:00:00+00:00",
            "checks": {
                "black_screen": "ENABLED",
                "audio_loss": "ENABLED",
            },
        },
    }

    # Stream ID consistency
    assert sample_snapshot["stream_id"] == sample_snapshot["status"]["stream_id"]
    # Schema version consistency
    assert sample_snapshot["schema_version"] == "1.0"
    assert sample_snapshot["status"]["schema_version"] == "1.0"


def test_worker_id_consistency_in_snapshot_sample():
    sample_snapshot = {
        "schema_version": "1.0",
        "update_id": "update-uuid",
        "update_type": "SNAPSHOT",
        "stream_id": "channel-01",
        "worker_id": "worker-local-01",
        "observed_at": "2026-08-27T10:00:00+00:00",
        "status": {
            "schema_version": "1.0",
            "stream_id": "channel-01",
            "status": "RUNNING",
            "health": "HEALTHY",
            "active_variant_count": 2,
            "queue_depth": 0,
            "checks": {
                "black_screen": "ENABLED",
                "audio_loss": "ENABLED",
            },
            "worker_id": "worker-local-01",
            "observed_at": "2026-08-27T10:00:00+00:00",
        },
    }

    assert sample_snapshot["worker_id"] == sample_snapshot["status"]["worker_id"]
    assert WORKER_ID_REGEX.match(sample_snapshot["worker_id"]) is not None


def test_removed_sample_payload_consistency():
    sample_removed = {
        "schema_version": "1.0",
        "update_id": "update-uuid-removed",
        "update_type": "REMOVED",
        "stream_id": "channel-01",
        "worker_id": "worker-local-01",
        "observed_at": "2026-08-27T10:10:00+00:00",
    }

    assert sample_removed["schema_version"] == "1.0"
    assert sample_removed["update_type"] == "REMOVED"
    assert "status" not in sample_removed
    assert WORKER_ID_REGEX.match(sample_removed["worker_id"]) is not None


def test_existing_command_and_alert_enums_unmodified():
    cmd_schema = load_schema("monitoring-command.schema.json")
    assert cmd_schema["properties"]["command_type"]["enum"] == [
        "START",
        "PAUSE",
        "RESUME",
        "STOP",
        "UPDATE_CONFIG",
    ]

    cmd_res_schema = load_schema("monitoring-command-result.schema.json")
    assert cmd_res_schema["properties"]["status"]["enum"] == [
        "APPLIED",
        "NOOP",
        "REJECTED",
        "FAILED",
    ]

    alert_schema = load_schema("alert.schema.json")
    assert alert_schema["properties"]["event_type"]["enum"] == [
        "BLACK_SCREEN",
        "REPEATED_BLACK_SCREEN",
        "AUDIO_LOSS",
        "VIDEO_FREEZE",
        "REPEATED_VIDEO_FREEZE",
        "RUNTIME_HEALTH",
    ]
    assert alert_schema["properties"]["state"]["enum"] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
        "DEGRADED",
        "RECOVERED",
    ]
