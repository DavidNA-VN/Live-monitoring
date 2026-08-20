from core.metrics import RuntimeMetricCollector


def test_metric_collector_drains_async_worker_metrics_per_cycle():
    collector = RuntimeMetricCollector()
    collector.record_analysis(
        duration_seconds=0.4,
        segment_age_seconds=2.0,
        ffmpeg_timed_out=False,
    )
    collector.record_analysis(
        duration_seconds=0.6,
        segment_age_seconds=3.0,
        ffmpeg_timed_out=True,
    )
    collector.record_retry()

    snapshot = collector.drain()

    assert snapshot.analysis_count == 2
    assert snapshot.analysis_duration_seconds_total == 1.0
    assert snapshot.segment_age_seconds_max == 3.0
    assert snapshot.ffmpeg_timeout_total == 1
    assert snapshot.retry_total == 1
    assert collector.drain().analysis_count == 0
