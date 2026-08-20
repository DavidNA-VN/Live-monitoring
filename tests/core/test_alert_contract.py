from datetime import datetime, timezone

import pytest

from models.alert import AlertCategory, AlertEnvelope


def envelope():
    now = datetime.now(timezone.utc)
    return AlertEnvelope(
        alert_id="alert-1",
        event_id="event-1",
        category=AlertCategory.CONTENT,
        event_type="BLACK_SCREEN",
        state="OPEN",
        stream_id="stream-1",
        check="black_screen",
        variant_id="720p",
        occurred_at=now,
        emitted_at=now,
        reason="continuous_black",
        attributes={"duration": "6.000000"},
    )


def test_alert_envelope_round_trips_without_terminal_parsing():
    original = envelope()

    decoded = AlertEnvelope.from_redis_fields(
        original.to_redis_fields()
    )

    assert decoded == original
    assert decoded.schema_version == "1.0"
    assert decoded.attributes["duration"] == "6.000000"


def test_unknown_alert_schema_fails_explicitly():
    fields = envelope().to_redis_fields()
    fields["schema_version"] = "99.0"

    with pytest.raises(ValueError, match="Unsupported alert schema"):
        AlertEnvelope.from_redis_fields(fields)
