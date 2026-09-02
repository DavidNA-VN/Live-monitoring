import json
import shutil

import pytest

from scripts.generate_monitoring_test_cases import (
    CASES,
    MonitoringCase,
    TimeRange,
    _audio_filter,
    _video_filter_graph,
    case_by_name,
    generate_cases,
)
from scripts.publish_live_hls import load_templates


def test_case_catalog_covers_each_implemented_check_and_clean_baseline():
    assert {item.name for item in CASES} == {
        "healthy",
        "black_screen",
        "audio_silence",
        "audio_missing",
        "video_freeze",
        "combined",
    }
    assert case_by_name("healthy").audio_track is True
    assert case_by_name("audio_missing").audio_track is False
    assert case_by_name("audio_missing").duration > 30.0
    assert case_by_name("combined").black_ranges
    assert case_by_name("combined").freeze_ranges
    assert case_by_name("combined").silence_ranges


def test_video_filter_combines_freeze_and_black_without_second_decode():
    graph = _video_filter_graph(case_by_name("combined"))

    assert graph is not None
    assert graph.count("freezeframes=") == 3
    assert graph.count("drawbox=") == 1
    assert graph.endswith("[video]")


def test_audio_filter_keeps_track_and_mutes_only_configured_ranges():
    expression = _audio_filter(case_by_name("audio_silence"))

    assert expression == "volume=volume=0:enable='between(t,4,39)'"


def test_invalid_case_ranges_are_rejected():
    with pytest.raises(ValueError, match="end before case duration"):
        MonitoringCase(
            name="invalid",
            duration=2.0,
            black_ranges=(TimeRange(0.0, 2.0),),
        )


def test_expected_catalog_is_json_serializable():
    payload = [
        {
            "name": item.name,
            "duration": item.duration,
            "notes": item.notes,
        }
        for item in CASES
    ]

    assert json.loads(json.dumps(payload))[0]["name"] == "healthy"


@pytest.mark.integration
def test_generated_healthy_case_has_two_aligned_variants(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable")
    output = tmp_path / "monitoring-cases"

    generate_cases(output, selected=("healthy",))

    _master, variants = load_templates(output / "healthy")
    expected = json.loads(
        (output / "healthy" / "expected.json").read_text(encoding="ascii")
    )
    assert len(variants) == expected["variant_count"] == 2
    assert len(variants[0].segments) == len(variants[1].segments) == 12
    assert sum(item.duration for item in variants[0].segments) == pytest.approx(
        expected["duration"], abs=0.05
    )
