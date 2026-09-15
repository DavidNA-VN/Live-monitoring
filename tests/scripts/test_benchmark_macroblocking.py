import json

import pytest

from scripts.benchmark_macroblocking import (
    BenchmarkInputError,
    canonical_fingerprint,
    evaluate,
    load_manifest,
    report_csv,
)


MANIFEST = {
    "schema_version": "1.0",
    "fixtures": [
        {
            "name": "positive",
            "ranges": [
                {
                    "start_seconds": 2.0,
                    "end_seconds": 4.0,
                    "label": "macroblocking",
                    "expected_area_ratio": [0.14, 0.18],
                }
            ],
        },
        {"name": "natural_grid", "ranges": []},
    ],
}


def predictions(*observations):
    return {
        "schema_version": "1.0",
        "configuration": {"fusion": "top_two", "sampling_fps": 5},
        "observations": list(observations),
    }


def item(fixture, timestamp, area, candidate):
    return {
        "fixture": fixture,
        "timestamp_seconds": timestamp,
        "affected_area_ratio": area,
        "candidate": candidate,
    }


def test_evaluate_scores_classification_and_area_band_independently():
    report = evaluate(
        MANIFEST,
        predictions(
            item("positive", 2.5, 0.16, True),
            item("positive", 3.0, 0.70, True),
            item("natural_grid", 2.0, 0.01, False),
        ),
    )
    assert report["counts"] == {"tp": 2, "fp": 0, "tn": 1, "fn": 0}
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["area_band_accuracy"] == 0.5
    assert report["mean_area_band_distance"] == pytest.approx(0.26)


def test_natural_grid_false_positive_reduces_precision():
    report = evaluate(
        MANIFEST,
        predictions(
            item("positive", 2.5, 0.16, True),
            item("natural_grid", 2.0, 0.20, True),
        ),
    )
    assert report["counts"]["fp"] == 1
    assert report["precision"] == 0.5


def test_configuration_fingerprint_is_canonical():
    assert canonical_fingerprint({"a": 1, "b": 2}) == canonical_fingerprint(
        {"b": 2, "a": 1}
    )
    assert canonical_fingerprint({"a": 1}) != canonical_fingerprint({"a": 2})


def test_manifest_rejects_positive_range_without_area_band(tmp_path):
    invalid = {
        "schema_version": "1.0",
        "fixtures": [
            {
                "name": "bad",
                "ranges": [
                    {
                        "start_seconds": 0,
                        "end_seconds": 1,
                        "label": "macroblocking",
                    }
                ],
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(invalid), encoding="ascii")
    with pytest.raises(BenchmarkInputError, match="area band"):
        load_manifest(path)


def test_csv_report_has_stable_columns():
    report = evaluate(
        MANIFEST,
        predictions(item("positive", 2.5, 0.16, True)),
    )
    lines = report_csv(report).splitlines()
    assert lines[0] == (
        "fixture,timestamp_seconds,truth,candidate,"
        "affected_area_ratio,area_in_expected_band"
    )


def test_report_derives_reproducible_performance_metrics():
    payload = predictions(item("positive", 2.5, 0.16, True))
    payload["performance"] = {
        "frame_count": 50,
        "wall_seconds": 2.0,
        "peak_bytes": 1024,
    }
    report = evaluate(MANIFEST, payload)
    assert report["performance"]["frames_per_second"] == 25.0
    assert report["performance"]["mean_frame_milliseconds"] == 40.0
