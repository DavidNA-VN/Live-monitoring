from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from core.live_runtime import (
    LiveMonitoringRuntime,
    LiveRuntimeSettings,
)
from core.redis_keys import RedisKeyBuilder
from core.segment_processor import SegmentProcessOutcome
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.processing import (
    SegmentClaim,
    SegmentClaimStatus,
    SegmentProcessingRecord,
    SegmentProcessingStatus,
)
from models.stream import StreamIdentity
from models.runtime import LiveCycleStats
from playlist.master_parser import Variant
from profiles.video_realtime import VideoRealtimeProfile
from core.context import MonitoringContext
from tests.factories.hls import make_snapshot


class ImmediateExecutor:
    def try_submit(self, function, *args, **kwargs):
        function(*args, **kwargs)
        return object()

    def shutdown(self, wait=True):
        pass


class RejectOnceExecutor(ImmediateExecutor):
    def __init__(self):
        self.rejected = False

    def try_submit(self, function, *args, **kwargs):
        if not self.rejected:
            self.rejected = True
            return None
        return super().try_submit(function, *args, **kwargs)


class FakeRedisPipeline:
    def delete(self, *_args, **_kwargs):
        return self

    def hset(self, *_args, **_kwargs):
        return self

    def expire(self, *_args, **_kwargs):
        return self

    def execute(self):
        return []


class FakeRedis:
    def pipeline(self, *args, **kwargs):
        return FakeRedisPipeline()


class FakeStateStore:
    def __init__(self, *, busy_sequences=None):
        self.key_builder = RedisKeyBuilder()
        self.redis = FakeRedis()
        self.lease_ms = 90_000
        self.max_attempts = 3
        self.records = {}
        self.busy_sequences = set(busy_sequences or [])

    def get_records(self, identities):
        return {
            identity: self.records.get(
                identity,
                SegmentProcessingRecord(
                    identity=identity,
                    status=None,
                    attempts=0,
                ),
            )
            for identity in identities
        }

    def claim(self, identity):
        if identity.sequence in self.busy_sequences:
            return SegmentClaim(
                identity=identity,
                status=SegmentClaimStatus.BUSY,
            )

        record = self.records.get(identity)
        attempts = record.attempts if record else 0

        if (
            record is not None
            and record.status == SegmentProcessingStatus.SUCCESS
        ):
            return SegmentClaim(
                identity=identity,
                status=SegmentClaimStatus.ALREADY_SUCCESSFUL,
                attempt=attempts,
            )

        claim = SegmentClaim(
            identity=identity,
            status=SegmentClaimStatus.ACQUIRED,
            lease_token=f"lease-{identity.sequence}",
            attempt=attempts + 1,
        )
        self.records[identity] = SegmentProcessingRecord(
            identity=identity,
            status=SegmentProcessingStatus.PROCESSING,
            attempts=attempts + 1,
        )
        return claim

    def renew(self, _claim):
        pass

    def mark_success(self, claim):
        self.records[claim.identity] = SegmentProcessingRecord(
            identity=claim.identity,
            status=SegmentProcessingStatus.SUCCESS,
            attempts=claim.attempt,
        )

    def mark_retryable_failure(self, claim, error):
        self.records[claim.identity] = SegmentProcessingRecord(
            identity=claim.identity,
            status=SegmentProcessingStatus.FAILED_RETRYABLE,
            attempts=claim.attempt,
            last_error=error,
        )

    def mark_terminal_failure(self, claim, error):
        self.records[claim.identity] = SegmentProcessingRecord(
            identity=claim.identity,
            status=SegmentProcessingStatus.FAILED_TERMINAL,
            attempts=claim.attempt,
            last_error=error,
        )


class FakeProcessor:
    analysis_profile = "video_realtime"
    requirements = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )

    def __init__(
        self,
        outcomes=None,
        *,
        name="black_screen",
    ):
        self.name = name
        self.outcomes = outcomes or {}
        self.processed = []
        self.committed = []

    @staticmethod
    def supports_segment(_segment):
        return True

    def process(self, segment, _analysis):
        self.processed.append(segment.sequence)
        queue = self.outcomes.get(segment.sequence)

        if queue:
            return queue.pop(0)

        return SegmentProcessOutcome.ok()

    def commit(self, segment, outcome):
        self.committed.append(segment.sequence)


class FakeVideoProfile:
    name = "video_realtime"
    provides = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )

    def __init__(self):
        self.analyzed = []

    @staticmethod
    def supports_segment(segment):
        return segment.has_video

    def analyze(self, segment):
        self.analyzed.append(segment.sequence)
        return SegmentAnalysisBundle(
            profile_name=self.name,
            video_realtime=VideoRealtimeAnalysis(
                checked=True
            ),
        )

    def close(self):
        pass


@dataclass
class FakeProcessResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self):
        return self.returncode == 0


class CountingProcessRunner:
    def __init__(self):
        self.calls = []

    def run(self, command, *, timeout):
        self.calls.append((command, timeout))
        return FakeProcessResult()


@dataclass
class ContextSequence:
    contexts: list[MonitoringContext]
    index: int = 0

    def next(self, *_args, **_kwargs):
        context = self.contexts[self.index]
        self.index += 1
        return context


def make_context(snapshot):
    variant = Variant(
        id=snapshot.variant_id,
        stable_id=snapshot.variant_stable_id,
        uri=snapshot.playlist_uri,
        bandwidth=None,
        resolution=None,
    )

    return MonitoringContext(
        master_url="https://example.test/master.m3u8",
        observed_at=snapshot.observed_at,
        variants=[variant],
        snapshots_by_variant={
            snapshot.variant_id: snapshot,
        },
        snapshot_errors_by_variant={},
    )


def make_runtime(
    monkeypatch,
    snapshots,
    processor,
    state_store,
    *,
    profile=None,
):
    contexts = ContextSequence(
        [make_context(snapshot) for snapshot in snapshots]
    )

    import core.live_runtime as live_runtime

    monkeypatch.setattr(
        live_runtime,
        "build_monitoring_context",
        contexts.next,
    )

    profile = profile or FakeVideoProfile()
    processors = (
        processor
        if isinstance(processor, list)
        else [processor]
    )
    runtime = LiveMonitoringRuntime(
        stream=StreamIdentity(
            stream_id="stream-1",
            master_url="https://example.test/master.m3u8",
        ),
        state_store=state_store,
        processors=processors,
        analysis_profiles=[profile],
        settings=LiveRuntimeSettings(
            max_workers=1,
            max_pending_tasks=0,
        ),
    )
    runtime.profile_scheduler.executor = ImmediateExecutor()
    runtime.test_profile = profile
    return runtime


def observed_time(seconds):
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )


def test_sliding_window_does_not_reprocess_successful_retained_segments(
    monkeypatch,
):
    snapshots = [
        make_snapshot([100, 101, 102], observed_at=observed_time(0)),
        make_snapshot([101, 102, 103], observed_at=observed_time(6)),
    ]
    processor = FakeProcessor()
    runtime = make_runtime(
        monkeypatch,
        snapshots,
        processor,
        FakeStateStore(),
    )

    runtime.run_cycle()
    runtime.run_cycle()

    assert processor.processed == [100, 101, 102, 103]
    assert processor.committed == [100, 101, 102, 103]


def test_new_segment_is_processed_once(monkeypatch):
    snapshots = [
        make_snapshot([100, 101, 102], observed_at=observed_time(0)),
        make_snapshot([101, 102, 103], observed_at=observed_time(6)),
        make_snapshot([102, 103, 104], observed_at=observed_time(12)),
    ]
    processor = FakeProcessor()
    runtime = make_runtime(
        monkeypatch,
        snapshots,
        processor,
        FakeStateStore(),
    )

    runtime.run_cycle()
    runtime.run_cycle()
    runtime.run_cycle()

    assert processor.processed.count(103) == 1
    assert processor.processed.count(104) == 1
    assert processor.processed == [100, 101, 102, 103, 104]


def test_retryable_retained_segment_can_be_retried(
    monkeypatch,
):
    snapshots = [
        make_snapshot([100, 101, 102], observed_at=observed_time(0)),
        make_snapshot([101, 102, 103], observed_at=observed_time(6)),
    ]
    processor = FakeProcessor(
        outcomes={
            101: [
                SegmentProcessOutcome.retry("ffmpeg timeout"),
                SegmentProcessOutcome.ok(),
            ]
        }
    )
    runtime = make_runtime(
        monkeypatch,
        snapshots,
        processor,
        FakeStateStore(),
    )

    runtime.run_cycle()
    runtime.run_cycle()

    assert processor.processed == [100, 101, 101, 102, 103]
    assert processor.committed == [100, 101, 102, 103]


def test_busy_earlier_sequence_prevents_overtaking(
    monkeypatch,
):
    snapshot = make_snapshot(
        [100, 101, 102],
        observed_at=observed_time(0),
    )
    processor = FakeProcessor()
    runtime = make_runtime(
        monkeypatch,
        [snapshot],
        processor,
        FakeStateStore(busy_sequences={100}),
    )

    stats = runtime.run_cycle()

    assert stats.scheduled_work_count == 3
    assert processor.processed == []
    assert processor.committed == []


def test_processor_capability_skips_ineligible_segment(
    monkeypatch,
):
    snapshot = make_snapshot([100, 101])
    snapshot.segments[0].has_video = False
    processor = FakeProcessor()
    processor.supports_segment = (
        lambda segment: segment.has_video
    )
    runtime = make_runtime(
        monkeypatch,
        [snapshot],
        processor,
        FakeStateStore(),
    )

    runtime.run_cycle()

    assert processor.processed == [101]
    assert processor.committed == [101]


def test_two_checks_share_one_profile_analysis_per_segment(
    monkeypatch,
):
    snapshot = make_snapshot([100, 101])
    first = FakeProcessor(name="black_screen")
    second = FakeProcessor(name="fake_video_check")
    runner = CountingProcessRunner()
    profile = VideoRealtimeProfile(runner=runner)
    runtime = make_runtime(
        monkeypatch,
        [snapshot],
        [first, second],
        FakeStateStore(),
        profile=profile,
    )

    stats = runtime.run_cycle()

    assert len(runner.calls) == 2
    assert all(
        call[0][0] == "ffmpeg" for call in runner.calls
    )
    assert first.processed == [100, 101]
    assert second.processed == [100, 101]
    assert stats.scheduled_work_count == 4


def test_backpressure_retains_segment_after_playlist_window_slides(
    monkeypatch,
):
    snapshots = [
        make_snapshot([100], observed_at=observed_time(0)),
        make_snapshot([101], observed_at=observed_time(6)),
    ]
    processor = FakeProcessor()
    runtime = make_runtime(
        monkeypatch,
        snapshots,
        processor,
        FakeStateStore(),
    )
    runtime.profile_scheduler.executor = RejectOnceExecutor()

    first_stats = runtime.run_cycle()
    second_stats = runtime.run_cycle()

    assert first_stats.backpressure_deferred_work_count == 1
    assert first_stats.queue_depth == 1
    assert processor.processed == [100, 101]
    assert second_stats.dropped_work_count == 0


def test_batch_limit_yields_between_large_variant_backlogs(monkeypatch):
    snapshot = make_snapshot([100, 101, 102])
    processor = FakeProcessor()
    runtime = make_runtime(
        monkeypatch,
        [snapshot],
        processor,
        FakeStateStore(),
    )
    scheduler = runtime.profile_scheduler
    scheduler.max_segments_per_batch = 1
    stats = LiveCycleStats(started_at=datetime.now(timezone.utc))

    scheduler.admit_snapshot(snapshot=snapshot, stats=stats)
    scheduler.dispatch_pending(stats=stats)
    scheduler.dispatch_pending(stats=stats)
    scheduler.dispatch_pending(stats=stats)

    assert processor.processed == [100, 101, 102]
