from datetime import datetime, timezone
import json
import pytest

from presentation.api.adapters.alert_codec import (
    AlertCodecError,
    parse_alert_fields,
)
from presentation.api.models import (
    AlertCategory,
    AlertState,
    EventType,
)


def valid_alert_fields() -> dict:
    return {
        "schema_version": "1.0",
        "alert_id": "alert-01",
        "event_id": "evt-100",
        "category": "content",
        "type": "BLACK_SCREEN",
        "state": "OPEN",
        "stream_id": "chan-01",
        "occurred_at": "2026-08-28T10:00:00+00:00",
        "emitted_at": "2026-08-28T10:00:01+00:00",
        "reason": "Black screen threshold reached",
        "payload": json.dumps({"duration": "5.0", "severity": "CRITICAL"}),
        "duration": "5.0",
        "severity": "CRITICAL",
        "check": "black_screen",
        "variant_id": "v-1080p",
        "variant_stable_id": "var-stable-1",
        "event_started_at": "2026-08-28T09:59:55+00:00",
    }


def test_1_parse_black_screen_open():
    fields = valid_alert_fields()
    dto = parse_alert_fields(fields, requested_stream_id="chan-01")

    assert dto.schema_version == "1.0"
    assert dto.alert_id == "alert-01"
    assert dto.event_id == "evt-100"
    assert dto.category == AlertCategory.CONTENT
    assert dto.event_type == EventType.BLACK_SCREEN
    assert dto.state == AlertState.OPEN
    assert dto.stream_id == "chan-01"
    assert dto.reason == "Black screen threshold reached"
    assert dto.attributes == {"duration": "5.0", "severity": "CRITICAL"}
    assert dto.check == "black_screen"
    assert dto.variant_id == "v-1080p"
    assert dto.variant_stable_id == "var-stable-1"
    assert dto.occurred_at == datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
    assert dto.emitted_at == datetime(2026, 8, 28, 10, 0, 1, tzinfo=timezone.utc)
    assert dto.event_started_at == datetime(2026, 8, 28, 9, 59, 55, tzinfo=timezone.utc)
    assert dto.event_ended_at is None


def test_2_parse_black_screen_resolved():
    fields = valid_alert_fields()
    fields["alert_id"] = "alert-02"
    fields["state"] = "RESOLVED"
    fields["event_ended_at"] = "2026-08-28T10:01:00+00:00"

    dto = parse_alert_fields(fields)
    assert dto.alert_id == "alert-02"
    assert dto.event_id == "evt-100"  # Keeps same event_id
    assert dto.state == AlertState.RESOLVED
    assert dto.event_ended_at == datetime(2026, 8, 28, 10, 1, 0, tzinfo=timezone.utc)


def test_3_parse_audio_loss_open():
    fields = valid_alert_fields()
    fields["type"] = "AUDIO_LOSS"
    fields["check"] = "audio_loss"
    fields["payload"] = json.dumps({"threshold_dbfs": "-35.0", "track": "0"})
    fields["threshold_dbfs"] = "-35.0"
    fields["track"] = "0"
    del fields["duration"]
    del fields["severity"]

    dto = parse_alert_fields(fields)
    assert dto.event_type == EventType.AUDIO_LOSS
    assert dto.check == "audio_loss"
    assert dto.attributes == {"threshold_dbfs": "-35.0", "track": "0"}


def test_4_parse_runtime_degraded():
    fields = valid_alert_fields()
    fields["category"] = "runtime"
    fields["type"] = "RUNTIME_HEALTH"
    fields["state"] = "DEGRADED"
    fields["reason"] = "playlist_poll_timeout"
    fields["payload"] = json.dumps({"component": "hls_poller"})
    fields["component"] = "hls_poller"
    del fields["duration"]
    del fields["severity"]

    dto = parse_alert_fields(fields)
    assert dto.category == AlertCategory.RUNTIME
    assert dto.event_type == EventType.RUNTIME_HEALTH
    assert dto.state == AlertState.DEGRADED
    assert dto.reason == "playlist_poll_timeout"


def test_5_accept_bytes_fields():
    fields = {k.encode("utf-8"): v.encode("utf-8") for k, v in valid_alert_fields().items()}
    dto = parse_alert_fields(fields)
    assert dto.alert_id == "alert-01"
    assert dto.stream_id == "chan-01"


def test_6_map_redis_type_to_api_event_type():
    for event_name in [
        "BLACK_SCREEN",
        "REPEATED_BLACK_SCREEN",
        "AUDIO_LOSS",
        "VIDEO_FREEZE",
        "REPEATED_VIDEO_FREEZE",
        "RUNTIME_HEALTH",
    ]:
        fields = valid_alert_fields()
        fields["type"] = event_name
        dto = parse_alert_fields(fields)
        assert dto.event_type.value == event_name


def test_parse_video_freeze_update_severity_contract():
    fields = valid_alert_fields()
    fields["type"] = "VIDEO_FREEZE"
    fields["state"] = "UPDATE"
    fields["check"] = "video_freeze"
    fields["reason"] = "freeze_alert_threshold"
    fields["payload"] = json.dumps(
        {"duration": "5.0", "severity": "ALERT"}
    )
    fields["severity"] = "ALERT"

    dto = parse_alert_fields(fields)

    assert dto.event_type is EventType.VIDEO_FREEZE
    assert dto.state is AlertState.UPDATE
    assert dto.attributes["severity"] == "ALERT"


def test_7_parse_empty_attributes():
    fields = valid_alert_fields()
    fields["payload"] = "{}"
    del fields["duration"]
    del fields["severity"]

    dto = parse_alert_fields(fields)
    assert dto.attributes == {}


def test_8_parse_flattened_attributes_matching_payload():
    fields = valid_alert_fields()
    # duration and severity match payload
    dto = parse_alert_fields(fields)
    assert dto.attributes["duration"] == "5.0"
    assert dto.attributes["severity"] == "CRITICAL"


def test_9_reject_flattened_attribute_mismatch():
    fields = valid_alert_fields()
    fields["duration"] = "10.0"  # In payload it is "5.0"
    with pytest.raises(AlertCodecError, match="Flattened attribute 'duration' value.*does not match payload"):
        parse_alert_fields(fields)


def test_10_reject_unknown_field_not_in_payload_attributes():
    fields = valid_alert_fields()
    fields["rogue_field"] = "unknown_val"  # not in payload
    with pytest.raises(AlertCodecError, match="Unexpected extra field 'rogue_field'"):
        parse_alert_fields(fields)


def test_11_reject_invalid_json_payload():
    fields = valid_alert_fields()
    fields["payload"] = "not-json{{"
    with pytest.raises(AlertCodecError, match="Alert payload is not valid JSON"):
        parse_alert_fields(fields)


def test_12_reject_payload_not_an_object():
    fields = valid_alert_fields()
    fields["payload"] = "[\"list\"]"
    with pytest.raises(AlertCodecError, match="Alert payload must be a JSON object"):
        parse_alert_fields(fields)


def test_13_reject_non_string_attribute_value():
    fields = valid_alert_fields()
    fields["payload"] = json.dumps({"num": 123})  # number instead of string
    fields["num"] = "123"
    with pytest.raises(AlertCodecError, match="All attribute keys and values in payload must be strings"):
        parse_alert_fields(fields)


def test_14_reject_invalid_category():
    fields = valid_alert_fields()
    fields["category"] = "invalid_cat"
    with pytest.raises(AlertCodecError, match="Invalid alert category"):
        parse_alert_fields(fields)


def test_15_reject_invalid_event_type():
    fields = valid_alert_fields()
    fields["type"] = "INVALID_EVENT_TYPE"
    with pytest.raises(AlertCodecError, match="Invalid alert event_type"):
        parse_alert_fields(fields)


def test_16_reject_invalid_state():
    fields = valid_alert_fields()
    fields["state"] = "INVALID_STATE"
    with pytest.raises(AlertCodecError, match="Invalid alert state"):
        parse_alert_fields(fields)


def test_17_reject_missing_required_field():
    for req_k in ["alert_id", "event_id", "category", "type", "state", "stream_id", "occurred_at", "emitted_at", "reason", "payload"]:
        fields = valid_alert_fields()
        del fields[req_k]
        with pytest.raises(AlertCodecError, match="Alert missing required fields"):
            parse_alert_fields(fields)


def test_18_reject_datetime_missing_timezone():
    fields = valid_alert_fields()
    fields["occurred_at"] = "2026-08-28T10:00:00"  # Naive
    with pytest.raises(AlertCodecError, match="must include timezone offset"):
        parse_alert_fields(fields)


def test_19_reject_stream_id_mismatch():
    fields = valid_alert_fields()
    fields["stream_id"] = "chan-01"
    with pytest.raises(AlertCodecError, match="Mismatched stream_id"):
        parse_alert_fields(fields, requested_stream_id="chan-other-99")


def test_20_reject_storage_id():
    # 1. In top-level
    fields1 = valid_alert_fields()
    fields1["storage_id"] = "storage-uuid-1"
    with pytest.raises(AlertCodecError, match="Field 'storage_id' is forbidden"):
        parse_alert_fields(fields1)

    # 2. In payload attributes
    fields2 = valid_alert_fields()
    fields2["payload"] = json.dumps({"storage_id": "storage-uuid-1"})
    fields2["storage_id"] = "storage-uuid-1"
    with pytest.raises(AlertCodecError, match="Field 'storage_id' is forbidden"):
        parse_alert_fields(fields2)


def test_21_reject_payload_attribute_that_shadows_canonical_field():
    fields = valid_alert_fields()
    fields["payload"] = json.dumps({"state": "RESOLVED"})

    with pytest.raises(AlertCodecError, match="conflicts with a canonical alert field"):
        parse_alert_fields(fields)
