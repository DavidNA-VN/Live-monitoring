from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest

from core.command_metrics import CommandMetricsCollector
from models.command_metrics import CommandMetricsSnapshot


def test_command_metrics_collector_initial_state():
    collector = CommandMetricsCollector()
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    snap = collector.snapshot("worker-test", observed_at=now)

    assert snap.worker_id == "worker-test"
    assert snap.observed_at == now
    assert snap.command_delivery_received_total == 0
    assert snap.command_applied_total == 0
    assert snap.command_noop_total == 0
    assert snap.command_rejected_total == 0
    assert snap.command_failed_total == 0
    assert snap.command_reclaimed_total == 0
    assert snap.command_duplicate_replay_total == 0
    assert snap.command_dead_letter_total == 0
    assert snap.command_oversized_total == 0
    assert snap.command_stale_total == 0
    assert snap.command_poll_failure_total == 0
    assert snap.command_processing_duration_ms_total == 0.0
    assert snap.command_pending_count == 0
    assert snap.command_deferred_count == 0
    assert snap.last_successful_poll_at is None
    assert snap.last_command_processed_at is None
    assert snap.last_error_code is None


def test_command_metrics_recording_transitions():
    collector = CommandMetricsCollector()
    collector.record_delivery(5)
    collector.record_reclaimed(2)
    collector.record_result("APPLIED", changed=True, duration_ms=10.5)
    collector.record_result("APPLIED", changed=False, duration_ms=5.0)
    collector.record_result("REJECTED", changed=False, duration_ms=2.0, error_code="INVALID_CONFIG")
    collector.record_result("FAILED", changed=False, duration_ms=8.0, error_code="INTERNAL_ERROR")
    collector.record_duplicate_replay(3)
    collector.record_dead_letter("BAD_PAYLOAD", oversized=True)
    collector.record_stale()
    collector.record_poll_failure()

    poll_time = datetime(2026, 8, 27, 10, 5, 0, tzinfo=timezone.utc)
    collector.record_poll_success(pending_count=3, deferred_count=1, at=poll_time)

    snap = collector.snapshot("worker-01")
    assert snap.command_delivery_received_total == 5
    assert snap.command_reclaimed_total == 2
    assert snap.command_applied_total == 1
    assert snap.command_noop_total == 1
    assert snap.command_rejected_total == 1
    assert snap.command_failed_total == 1
    assert snap.command_duplicate_replay_total == 3
    assert snap.command_dead_letter_total == 1
    assert snap.command_oversized_total == 1
    assert snap.command_stale_total == 1
    assert snap.command_poll_failure_total == 1
    assert snap.command_processing_duration_ms_total == pytest.approx(25.5)
    assert snap.command_processing_duration_ms_max == pytest.approx(10.5)
    assert snap.last_command_processing_duration_ms == pytest.approx(8.0)
    assert snap.command_pending_count == 3
    assert snap.command_deferred_count == 1
    assert snap.last_successful_poll_at == poll_time
    assert snap.last_error_code == "BAD_PAYLOAD"


def test_command_metrics_snapshot_to_redis_hash():
    collector = CommandMetricsCollector()
    collector.record_delivery(1)
    collector.record_result("APPLIED", changed=True, duration_ms=12.3456)
    snap = collector.snapshot("worker-hash-test")

    h = snap.to_redis_hash()
    assert isinstance(h, dict)
    assert h["worker_id"] == "worker-hash-test"
    assert h["schema_version"] == "1.0"
    assert h["command_applied_total"] == "1"
    assert float(h["command_processing_duration_ms_total"]) == pytest.approx(12.346, abs=1e-3)
    assert "observed_at" in h


def test_command_metrics_concurrent_increments():
    collector = CommandMetricsCollector()

    def _worker():
        for _ in range(100):
            collector.record_delivery(1)
            collector.record_result("APPLIED", changed=True, duration_ms=1.0)
            collector.record_duplicate_replay(1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker) for _ in range(8)]
        for f in futures:
            f.result()

    snap = collector.snapshot("worker-concurrent")
    assert snap.command_delivery_received_total == 800
    assert snap.command_applied_total == 800
    assert snap.command_duplicate_replay_total == 800
    assert snap.command_processing_duration_ms_total == pytest.approx(800.0)


def test_command_metrics_rejects_unknown_result_status():
    collector = CommandMetricsCollector()
    with pytest.raises(ValueError, match="Unsupported command result status"):
        collector.record_result("UNKNOWN", changed=False, duration_ms=1.0)
    assert collector.snapshot("worker-test").command_processing_duration_ms_total == 0
