from dataclasses import asdict
from argparse import Namespace

import pytest

from scripts.run_capacity_benchmark import (
    BenchmarkConfiguration,
    MetricSample,
    ResourceSample,
    counter_delta,
    linear_slope,
    parse_redis_metrics,
    summarize_run,
    _checks,
    _validate,
)


CONFIG = BenchmarkConfiguration(
    source_kind="local_fixture",
    checks="all",
    video_workers=4,
    audio_workers=1,
    global_process_limit=4,
    per_stream_process_limit=4,
    variant_selection="highest_quality",
    publisher_speed=1.0,
)


def test_parse_redis_metrics_ignores_non_numeric_fields():
    assert parse_redis_metrics(
        {b"queue_lag_seconds": b"1.25", b"admission_mode": b"coverage"}
    ) == {"queue_lag_seconds": 1.25}


def test_counter_delta_only_uses_monotonic_counters():
    assert counter_delta(
        {"perf_video_work_completed_total": 4.0, "queue_depth": 8.0},
        {"perf_video_work_completed_total": 9.0, "queue_depth": 2.0},
    ) == {"perf_video_work_completed_total": 5.0}


def test_linear_slope_reports_queue_growth_rate():
    assert linear_slope([(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]) == 1.0


def test_summary_passes_stable_run_and_reports_counter_deltas():
    result = summarize_run(
        configuration=CONFIG,
        warmup_seconds=1.0,
        steady_seconds=3.0,
        cooldown_seconds=1.0,
        metric_samples=[
            MetricSample(
                0.0,
                {
                    "queue_lag_seconds": 2.0,
                    "perf_video_realtime_work_completed_total": 10.0,
                    "perf_resource_video_decode_work_completed_total": 10.0,
                    "dropped_media_segments_total": 0.0,
                    "coverage_gap_segment_total": 0.0,
                    "peak_active_media_processes": 2.0,
                    "perf_video_realtime_profile_execution_seconds_p95": 0.2,
                },
            ),
            MetricSample(
                3.0,
                {
                    "queue_lag_seconds": 2.0,
                    "perf_video_realtime_work_completed_total": 15.0,
                    "perf_resource_video_decode_work_completed_total": 15.0,
                    "dropped_media_segments_total": 0.0,
                    "coverage_gap_segment_total": 0.0,
                    "peak_active_media_processes": 3.0,
                    "perf_video_realtime_profile_execution_seconds_p95": 0.5,
                },
            ),
        ],
        resource_samples=[ResourceSample(0.0, 50.0, 100, 2)],
        max_queue_lag_seconds=6.0,
        max_queue_slope=0.05,
        allow_dropped_segments=0,
    )

    assert result.passed
    assert result.work_completed == 5
    assert result.work_completed_per_second == 5 / 3
    assert result.peak_active_media_processes == 3
    assert result.profile_execution_p95_max_seconds == {
        "video_realtime": 0.5
    }


def test_summary_fails_growing_lag_drops_and_timeout():
    result = summarize_run(
        configuration=CONFIG,
        warmup_seconds=1.0,
        steady_seconds=2.0,
        cooldown_seconds=0.0,
        metric_samples=[
            MetricSample(0.0, {"queue_lag_seconds": 1.0}),
            MetricSample(1.0, {"queue_lag_seconds": 1.0}),
            MetricSample(
                2.0,
                {
                    "queue_lag_seconds": 9.0,
                    "dropped_media_segments_total": 2.0,
                    "perf_audio_realtime_work_timed_out_total": 1.0,
                },
            ),
        ],
        resource_samples=[],
        max_queue_lag_seconds=6.0,
        max_queue_slope=0.05,
        allow_dropped_segments=0,
    )

    assert not result.passed
    assert len(result.failures) == 4


def test_summary_separates_warmup_steady_and_cooldown_counters():
    result = summarize_run(
        configuration=CONFIG,
        warmup_seconds=2.0,
        steady_seconds=4.0,
        cooldown_seconds=2.0,
        metric_samples=[
            MetricSample(
                1.0,
                {
                    "queue_lag_seconds": 3.0,
                    "dropped_media_segments_total": 2.0,
                    "coverage_gap_segment_total": 2.0,
                },
            ),
            MetricSample(
                2.0,
                {
                    "queue_lag_seconds": 2.0,
                    "dropped_media_segments_total": 3.0,
                    "coverage_gap_segment_total": 3.0,
                },
            ),
            MetricSample(
                6.0,
                {
                    "queue_lag_seconds": 1.0,
                    "dropped_media_segments_total": 4.0,
                    "coverage_gap_segment_total": 5.0,
                },
            ),
            MetricSample(
                8.0,
                {
                    "queue_lag_seconds": 0.0,
                    "dropped_media_segments_total": 6.0,
                    "coverage_gap_segment_total": 8.0,
                },
            ),
        ],
        resource_samples=[],
        max_queue_lag_seconds=6.0,
        max_queue_slope=0.05,
        allow_dropped_segments=1,
    )

    assert result.warmup_dropped_media_segments == 3
    assert result.steady_dropped_media_segments == 1
    assert result.cooldown_dropped_media_segments == 2
    assert result.warmup_coverage_gap_segments == 3
    assert result.steady_coverage_gap_segments == 2
    assert result.cooldown_coverage_gap_segments == 3
    assert result.dropped_media_segments == 6
    assert result.coverage_gap_segments == 8
    assert result.passed


def test_all_check_set_includes_every_current_detector_lane():
    assert _checks("all") == (True, True, True, True)
    assert _checks("video") == (True, True, False, True)
    assert _checks("audio") == (False, False, True, False)


def test_external_url_is_not_part_of_serialized_result():
    result = summarize_run(
        configuration=BenchmarkConfiguration(
            source_kind="external_live",
            checks="all",
            video_workers=2,
            audio_workers=1,
            global_process_limit=3,
            per_stream_process_limit=3,
            variant_selection="highest_quality",
            publisher_speed=1.0,
        ),
        warmup_seconds=1.0,
        steady_seconds=1.0,
        cooldown_seconds=0.0,
        metric_samples=[
            MetricSample(0.0, {"queue_lag_seconds": 0.0}),
            MetricSample(1.0, {"queue_lag_seconds": 0.0}),
        ],
        resource_samples=[],
        max_queue_lag_seconds=6.0,
        max_queue_slope=0.05,
        allow_dropped_segments=0,
    )

    assert "bpk-token" not in str(asdict(result))
    assert asdict(result)["configuration"]["source_kind"] == "external_live"


def test_external_url_rejects_local_publisher_speed_matrix():
    args = Namespace(
        per_stream_process_limit=3,
        warmup_seconds=1.0,
        steady_seconds=1.0,
        sample_interval=0.5,
        window_size=6,
        retention_segments=12,
        max_queue_lag_seconds=12.0,
        cooldown_seconds=0.0,
        allow_dropped_segments=0,
        url="https://example.test/live.m3u8",
        publisher_speeds=[1.0, 2.0],
    )

    with pytest.raises(ValueError, match="only applies to local fixtures"):
        _validate(args)
