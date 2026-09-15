from scripts.verify_monitoring_test_cases import (
    expected_content_alert_counts,
    expected_content_alerts,
    observed_content_alert_counts,
    observed_content_alerts,
)


def test_expected_content_alerts_expands_states():
    expected = {
        "expected_alerts": [
            {"event_type": "AUDIO_LOSS", "states": ["OPEN", "RESOLVED"]},
            {"event_type": "MACROBLOCKING", "states": ["OPEN"]},
        ]
    }

    assert expected_content_alerts(expected) == {
        ("AUDIO_LOSS", "OPEN"),
        ("AUDIO_LOSS", "RESOLVED"),
        ("MACROBLOCKING", "OPEN"),
    }


def test_observed_content_alerts_ignores_runtime_entries():
    entries = [
        {"category": "runtime", "type": "RUNTIME_HEALTH", "state": "DEGRADED"},
        {"category": "content", "type": "BLACK_SCREEN", "state": "OPEN"},
    ]

    assert observed_content_alerts(entries) == {("BLACK_SCREEN", "OPEN")}


def test_alert_counts_preserve_duplicates_and_expected_multiplicity():
    expected = {
        "expected_alerts": [
            {
                "event_type": "AUDIO_LOSS",
                "states": ["OPEN", "RESOLVED"],
                "per_variant": 1,
            }
        ]
    }
    entries = [
        {"category": "content", "type": "AUDIO_LOSS", "state": "OPEN"},
        {"category": "content", "type": "AUDIO_LOSS", "state": "OPEN"},
    ]

    assert expected_content_alert_counts(expected)[("AUDIO_LOSS", "OPEN")] == 1
    assert observed_content_alert_counts(entries)[("AUDIO_LOSS", "OPEN")] == 2
