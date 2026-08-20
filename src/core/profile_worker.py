from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event, Thread
from datetime import datetime, timezone
from time import monotonic

from core.analysis_profile import AnalysisProfile
from core.redis_client import RedisUnavailableError
from core.metrics import RuntimeMetricCollector
from core.segment_admission import ProfileSegmentIdentity
from core.segment_processor import SegmentProcessor
from core.segment_state import RedisSegmentStateStore, SegmentLeaseLostError
from models.analysis import SegmentAnalysisBundle
from models.processing import (
    SegmentClaim,
    SegmentClaimStatus,
    SegmentProcessingIdentity,
)
from models.segment import Segment


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessorSegmentWork:
    processor: SegmentProcessor
    identity: SegmentProcessingIdentity


@dataclass(frozen=True)
class ProfileSegmentWork:
    admission_identity: ProfileSegmentIdentity
    segment: Segment
    processors: tuple[ProcessorSegmentWork, ...]


class ProfileWorkerCoordinator:
    """Owns claim, lease, analysis and commit for admitted profile work."""

    def __init__(
        self,
        state_store: RedisSegmentStateStore,
        metrics: RuntimeMetricCollector | None = None,
    ) -> None:
        self.state_store = state_store
        self.metrics = metrics or RuntimeMetricCollector()

    def process_batch(
        self,
        profile: AnalysisProfile,
        work: list[ProfileSegmentWork],
    ) -> None:
        blocked_processors: set[str] = set()
        for item in work:
            try:
                claimed = self._claim_processors(item, blocked_processors)
            except RedisUnavailableError:
                return
            if not claimed:
                continue
            heartbeats = self._start_heartbeats(claimed)
            try:
                analysis_started = monotonic()
                analysis = self._analyze(
                    profile, item.segment, claimed, blocked_processors
                )
                video = analysis.video_realtime if analysis else None
                self.metrics.record_analysis(
                    duration_seconds=monotonic() - analysis_started,
                    segment_age_seconds=self._segment_age(item.segment),
                    ffmpeg_timed_out=bool(video and video.timed_out),
                )
                if analysis is None:
                    continue
                for processor, claim in claimed:
                    if not self._process_claimed_segment(
                        processor, item.segment, claim, analysis
                    ):
                        blocked_processors.add(processor.name)
            finally:
                self._stop_heartbeats(heartbeats)

    def _claim_processors(
        self,
        work: ProfileSegmentWork,
        blocked: set[str],
    ) -> list[tuple[SegmentProcessor, SegmentClaim]]:
        claimed = []
        for processor_work in work.processors:
            processor = processor_work.processor
            if processor.name in blocked:
                continue
            claim = self.state_store.claim(processor_work.identity)
            if claim.status == SegmentClaimStatus.ALREADY_SUCCESSFUL:
                continue
            if claim.status == SegmentClaimStatus.BUSY:
                blocked.add(processor.name)
                continue
            if claim.status in (
                SegmentClaimStatus.RETRY_EXHAUSTED,
                SegmentClaimStatus.TERMINAL_FAILURE,
            ):
                continue
            if not claim.acquired:
                blocked.add(processor.name)
                continue
            claimed.append((processor, claim))
        return claimed

    def _analyze(
        self,
        profile: AnalysisProfile,
        segment: Segment,
        claimed: list[tuple[SegmentProcessor, SegmentClaim]],
        blocked: set[str],
    ) -> SegmentAnalysisBundle | None:
        try:
            return profile.analyze(segment)
        except Exception as exc:
            logger.exception(
                "Unhandled analysis profile failure profile=%s variant=%s seq=%s",
                profile.name,
                segment.variant_id,
                segment.sequence,
                extra={
                    "event_name": "analysis_profile_failure",
                    "profile_name": profile.name,
                    "variant_id": segment.variant_id,
                    "segment_sequence": segment.sequence,
                },
            )
            for processor, claim in claimed:
                try:
                    self.state_store.mark_retryable_failure(claim, str(exc))
                except (RedisUnavailableError, SegmentLeaseLostError):
                    pass
                self.metrics.record_retry()
                blocked.add(processor.name)
            return None

    def _process_claimed_segment(
        self,
        processor: SegmentProcessor,
        segment: Segment,
        claim: SegmentClaim,
        analysis: SegmentAnalysisBundle,
    ) -> bool:
        try:
            outcome = processor.process(segment, analysis)
            if not outcome.success:
                if outcome.retryable:
                    self.state_store.mark_retryable_failure(
                        claim, outcome.error or "retryable processing failure"
                    )
                    self.metrics.record_retry()
                    return False
                self.state_store.mark_terminal_failure(
                    claim, outcome.error or "terminal processing failure"
                )
                return True
            try:
                processor.commit(segment, outcome)
            except Exception as exc:
                self.state_store.mark_retryable_failure(claim, str(exc))
                self.metrics.record_retry()
                return False
            self.state_store.mark_success(claim)
            return True
        except (RedisUnavailableError, SegmentLeaseLostError):
            return False
        except Exception as exc:
            logger.exception(
                "Unhandled processor failure check=%s variant=%s seq=%s",
                processor.name,
                segment.variant_id,
                segment.sequence,
                extra={
                    "event_name": "segment_processor_failure",
                    "check_name": processor.name,
                    "variant_id": segment.variant_id,
                    "segment_sequence": segment.sequence,
                },
            )
            try:
                self.state_store.mark_retryable_failure(claim, str(exc))
            except (RedisUnavailableError, SegmentLeaseLostError):
                pass
            self.metrics.record_retry()
            return False

    @staticmethod
    def _segment_age(segment: Segment) -> float:
        if segment.program_date_time is None:
            return 0.0
        end = segment.program_date_time.timestamp() + segment.duration
        return max(0.0, datetime.now(timezone.utc).timestamp() - end)

    def _start_heartbeats(
        self,
        claimed: list[tuple[SegmentProcessor, SegmentClaim]],
    ) -> list[tuple[Event, Thread]]:
        heartbeats = []
        for _, claim in claimed:
            stop_event = Event()
            thread = Thread(
                target=self._lease_heartbeat,
                args=(claim, stop_event),
                name="media-monitor-lease-heartbeat",
                daemon=True,
            )
            thread.start()
            heartbeats.append((stop_event, thread))
        return heartbeats

    @staticmethod
    def _stop_heartbeats(heartbeats: list[tuple[Event, Thread]]) -> None:
        for stop_event, _ in heartbeats:
            stop_event.set()
        for _, thread in heartbeats:
            thread.join(timeout=1.0)

    def _lease_heartbeat(
        self,
        claim: SegmentClaim,
        stop_event: Event,
    ) -> None:
        interval = max(1.0, self.state_store.lease_ms / 1000.0 / 3.0)
        while not stop_event.wait(interval):
            try:
                self.state_store.renew(claim)
            except (RedisUnavailableError, SegmentLeaseLostError) as exc:
                logger.warning(
                    "Unable to renew segment lease seq=%s: %s",
                    claim.identity.sequence,
                    exc,
                )
                return
