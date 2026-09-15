from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
from threading import Event, Thread
from datetime import datetime, timezone
from time import monotonic

from core.analysis_profile import AnalysisProfile, AnalysisResourceClass
from core.redis_client import RedisUnavailableError
from core.metrics import RuntimeMetricCollector
from core.media_process_budget import ProcessGate
from core.segment_admission import ProfileSegmentIdentity
from core.segment_processor import SegmentProcessor
from core.segment_state import RedisSegmentStateStore, SegmentLeaseLostError
from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
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


@dataclass(frozen=True)
class _AnalysisResult:
    analysis: SegmentAnalysisBundle | None
    duration_seconds: float
    failed_processor_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _PendingAnalysis:
    item: ProfileSegmentWork
    claimed: tuple[tuple[SegmentProcessor, SegmentClaim], ...]
    heartbeats: tuple[tuple[Event, Thread], ...]
    future: Future[_AnalysisResult]


class ProfileWorkerCoordinator:
    """Owns claim, lease, analysis and commit for admitted profile work."""

    def __init__(
        self,
        state_store: RedisSegmentStateStore,
        metrics: RuntimeMetricCollector | None = None,
        media_process_gate: ProcessGate | None = None,
        max_parallel_analysis: int = 2,
    ) -> None:
        if max_parallel_analysis <= 0:
            raise ValueError("max_parallel_analysis must be > 0")
        self.state_store = state_store
        self.metrics = metrics or RuntimeMetricCollector()
        self.media_process_gate = media_process_gate
        self.max_parallel_analysis = max_parallel_analysis
        self.stop_event = Event()

    def request_stop(self) -> None:
        self.stop_event.set()

    def process_batch(
        self,
        profile: AnalysisProfile,
        work: list[ProfileSegmentWork],
        *,
        max_parallel_analysis: int | None = None,
        on_item_completed: Callable[[ProfileSegmentIdentity], None]
        | None = None,
    ) -> None:
        parallel_limit = (
            self.max_parallel_analysis
            if max_parallel_analysis is None
            else max_parallel_analysis
        )
        if parallel_limit <= 0:
            raise ValueError("max_parallel_analysis must be > 0")
        blocked_processors: set[str] = set()
        pending: deque[_PendingAnalysis] = deque()
        work_iterator = iter(work)
        exhausted = False
        worker_count = min(parallel_limit, max(1, len(work)))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="media-monitor-analysis",
        ) as analysis_executor:
            while pending or not exhausted:
                while (
                    not exhausted
                    and not self.stop_event.is_set()
                    and len(pending) < parallel_limit
                ):
                    try:
                        item = next(work_iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    prepared = self._submit_analysis(
                        analysis_executor,
                        profile,
                        item,
                        blocked_processors,
                    )
                    if prepared is not None:
                        pending.append(prepared)

                if not pending:
                    break

                current = pending.popleft()
                if self.stop_event.is_set():
                    self._defer_pending_analysis(
                        current,
                        reason="worker shutdown before ordered commit",
                    )
                    continue
                self._commit_analysis_result(
                    profile,
                    current,
                    blocked_processors,
                    on_item_completed,
                )

            while pending:
                self._defer_pending_analysis(
                    pending.popleft(),
                    reason="worker shutdown before ordered commit",
                )

    def _submit_analysis(
        self,
        executor: ThreadPoolExecutor,
        profile: AnalysisProfile,
        item: ProfileSegmentWork,
        blocked_processors: set[str],
    ) -> _PendingAnalysis | None:
        try:
            claimed = self._claim_processors(item, blocked_processors)
        except RedisUnavailableError:
            self.request_stop()
            return None
        if not claimed:
            return None
        heartbeats = self._start_heartbeats(claimed)
        try:
            future = executor.submit(
                self._run_analysis,
                profile,
                item,
                tuple(claimed),
            )
        except Exception:
            self._stop_heartbeats(heartbeats)
            self._relinquish_claims(
                claimed, "unable to submit profile analysis"
            )
            raise
        return _PendingAnalysis(
            item=item,
            claimed=tuple(claimed),
            heartbeats=tuple(heartbeats),
            future=future,
        )

    def _run_analysis(
        self,
        profile: AnalysisProfile,
        item: ProfileSegmentWork,
        claimed: tuple[tuple[SegmentProcessor, SegmentClaim], ...],
    ) -> _AnalysisResult:
        started = monotonic()
        failed_processors: set[str] = set()
        analysis = self._analyze(
            profile,
            item.segment,
            list(claimed),
            failed_processors,
        )
        return _AnalysisResult(
            analysis=analysis,
            duration_seconds=monotonic() - started,
            failed_processor_names=frozenset(failed_processors),
        )

    def _commit_analysis_result(
        self,
        profile: AnalysisProfile,
        pending: _PendingAnalysis,
        blocked_processors: set[str],
        on_item_completed: Callable[[ProfileSegmentIdentity], None] | None,
    ) -> None:
        try:
            result = pending.future.result()
            analysis = result.analysis
            self.metrics.record_analysis(
                duration_seconds=result.duration_seconds,
                segment_age_seconds=self._segment_age(pending.item.segment),
                ffmpeg_timed_out=bool(
                    analysis and analysis.media_process_timed_out
                ),
            )
            if analysis is None:
                blocked_processors.update(result.failed_processor_names)
                self._record_work_state(profile, completed=False)
                return
            self.metrics.record_profile_result(analysis)
            if analysis.media_process_timed_out:
                for dimension in self._metric_dimensions(profile):
                    self.metrics.record_work_timed_out(dimension)
            work_succeeded = True
            for processor, claim in pending.claimed:
                if processor.name in blocked_processors:
                    self._relinquish_claims(
                        ((processor, claim),),
                        "ordered commit blocked by an earlier segment",
                    )
                    work_succeeded = False
                    continue
                if not self._process_claimed_segment(
                    processor,
                    pending.item.segment,
                    claim,
                    analysis,
                ):
                    blocked_processors.add(processor.name)
                    work_succeeded = False
            item_completed = (
                work_succeeded
                and len(pending.claimed) == len(pending.item.processors)
            )
            self._record_work_state(profile, completed=item_completed)
            if item_completed and on_item_completed is not None:
                self._notify_item_completed(
                    on_item_completed,
                    pending.item.admission_identity,
                )
        finally:
            self._stop_heartbeats(list(pending.heartbeats))

    def _defer_pending_analysis(
        self,
        pending: _PendingAnalysis,
        *,
        reason: str,
    ) -> None:
        try:
            pending.future.result()
            self._relinquish_claims(pending.claimed, reason)
        finally:
            self._stop_heartbeats(list(pending.heartbeats))

    def _relinquish_claims(
        self,
        claimed: tuple[tuple[SegmentProcessor, SegmentClaim], ...]
        | list[tuple[SegmentProcessor, SegmentClaim]],
        reason: str,
    ) -> None:
        for _processor, claim in claimed:
            try:
                self.state_store.relinquish(claim, reason)
            except (RedisUnavailableError, SegmentLeaseLostError):
                pass

    @staticmethod
    def _notify_item_completed(
        callback: Callable[[ProfileSegmentIdentity], None],
        identity: ProfileSegmentIdentity,
    ) -> None:
        try:
            callback(identity)
        except Exception:
            logger.exception(
                "Unable to acknowledge completed admission item "
                "profile=%s variant=%s seq=%s",
                identity.profile_name,
                identity.variant_stable_id,
                identity.sequence,
            )

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
            requirements: frozenset[AnalysisRequirement] = frozenset(
                requirement
                for processor, _claim in claimed
                for requirement in processor.requirements
            )
            uses_media_process = getattr(
                profile,
                "resource_class",
                AnalysisResourceClass.VIDEO_DECODE,
            ) in (
                AnalysisResourceClass.VIDEO_DECODE,
                AnalysisResourceClass.AUDIO_DECODE,
                AnalysisResourceClass.EXPENSIVE,
            )
            if not uses_media_process or self.media_process_gate is None:
                return self._execute_profile(profile, segment, requirements)
            gate_started = monotonic()
            self.media_process_gate.acquire()
            try:
                for dimension in self._metric_dimensions(profile):
                    self.metrics.record_process_gate_wait(
                        dimension, monotonic() - gate_started
                    )
                return self._execute_profile(profile, segment, requirements)
            finally:
                self.media_process_gate.release()
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

    def _execute_profile(
        self,
        profile: AnalysisProfile,
        segment: Segment,
        requirements: frozenset[AnalysisRequirement],
    ) -> SegmentAnalysisBundle:
        started = monotonic()
        try:
            return profile.analyze(segment, requirements=requirements)
        finally:
            for dimension in self._metric_dimensions(profile):
                self.metrics.record_profile_execution(
                    dimension, monotonic() - started
                )

    def _record_work_state(
        self, profile: AnalysisProfile, *, completed: bool
    ) -> None:
        recorder = (
            self.metrics.record_work_completed
            if completed
            else self.metrics.record_work_failed
        )
        for dimension in self._metric_dimensions(profile):
            recorder(dimension)

    @staticmethod
    def _metric_dimensions(profile: AnalysisProfile) -> tuple[str, str]:
        resource_class = getattr(
            profile,
            "resource_class",
            AnalysisResourceClass.VIDEO_DECODE,
        )
        return (
            profile.name,
            f"resource_{AnalysisResourceClass(resource_class).value}",
        )

    def _process_claimed_segment(
        self,
        processor: SegmentProcessor,
        segment: Segment,
        claim: SegmentClaim,
        analysis: SegmentAnalysisBundle,
    ) -> bool:
        try:
            processing_started = monotonic()
            try:
                outcome = processor.process(segment, analysis)
            finally:
                self.metrics.record_result_processing(
                    analysis.profile_name, monotonic() - processing_started
                )
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
                commit_started = monotonic()
                try:
                    processor.commit(segment, outcome)
                finally:
                    self.metrics.record_result_commit(
                        analysis.profile_name, monotonic() - commit_started
                    )
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
