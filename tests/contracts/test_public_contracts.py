import json
from pathlib import Path


CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as schema_file:
        return json.load(schema_file)


def test_all_mvp_contracts_use_schema_version_1():
    names = (
        "stream-config.schema.json",
        "monitoring-command.schema.json",
        "monitoring-command-result.schema.json",
        "runtime-status.schema.json",
        "runtime-status-update.schema.json",
        "worker-heartbeat.schema.json",
        "worker-command-metrics.schema.json",
        "alert.schema.json",
    )

    for name in names:
        schema = load_schema(name)
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["properties"]["schema_version"]["const"] == "1.0"
        assert "schema_version" in schema["required"]


def test_alert_contract_matches_current_detection_cases():
    schema = load_schema("alert.schema.json")
    properties = schema["properties"]

    assert properties["event_type"]["enum"] == [
        "BLACK_SCREEN",
        "REPEATED_BLACK_SCREEN",
        "AUDIO_LOSS",
        "RUNTIME_HEALTH",
    ]
    assert properties["state"]["enum"] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
        "DEGRADED",
        "RECOVERED",
    ]
    assert {
        "alert_id",
        "event_id",
        "stream_id",
        "event_type",
        "state",
        "reason",
        "attributes",
    }.issubset(schema["required"])
    assert "variant_stable_id" in properties


def test_stream_config_exposes_only_public_identity():
    schema = load_schema("stream-config.schema.json")

    assert "stream_id" in schema["properties"]
    assert "storage_id" not in schema["properties"]
    assert set(schema["properties"]["checks"]["properties"]) == {
        "black_screen",
        "audio_loss",
    }


def test_monitoring_commands_cover_mvp_lifecycle():
    schema = load_schema("monitoring-command.schema.json")

    assert schema["properties"]["command_type"]["enum"] == [
        "START",
        "PAUSE",
        "RESUME",
        "STOP",
        "UPDATE_CONFIG",
    ]

    result_schema = load_schema("monitoring-command-result.schema.json")
    assert result_schema["properties"]["command_type"]["enum"] == schema[
        "properties"
    ]["command_type"]["enum"]
    assert result_schema["properties"]["status"]["enum"] == [
        "APPLIED",
        "NOOP",
        "REJECTED",
        "FAILED",
    ]


def test_runtime_status_is_not_coupled_to_internal_redis_keys():
    schema = load_schema("runtime-status.schema.json")
    properties = schema["properties"]

    assert "stream_id" in properties
    assert "health" in properties
    assert "queue_depth" in properties
    assert not any("redis" in name for name in properties)
