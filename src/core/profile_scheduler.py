import logging
from collections.abc import Mapping
from threading import BoundedSemaphore, Lock

from core.analysis_profile import AnalysisProfile, AnalysisResourceClass
from core.bounded_executor import BoundedExecutor
from core.profile_worker import (
    ProcessorSegmentWork,
    ProfileSegmentWork,
    ProfileWorkerCoordinator,
)
from core.metrics import RuntimeMetricCollector
from core.segment_admission import (
    AdmissionDrop,
    AdmissionDropReason,
    AdmissionQueue,
    AdmittedProfileSegment,
    ProfileSegmentIdentity,
    SegmentAdmissionQueue,
)
from core.segment_processor import SegmentProcessor
from core.segment_state import RedisSegmentStateStore
from models.playlist_snapshot import MediaPlaylistSnapshot
from models.analysis import ResourcePoolLimit
from models.processing import (
    SegmentProcessingIdentity,
    SegmentProcessingRecord,
    SegmentProcessingStatus,
)
from models.runtime import LiveCycleStats
from models.stream import StreamIdentity


logger = logging.getLogger(__name__)


class ProfileScheduler:
    """Plans admitted profile work and dispatches it to resource pools."""

    def __init__(
        self,
        *,
        stream: StreamIdentity,
        state_store: RedisSegmentStateStore,
        processors: list[SegmentProcessor],
        analysis_profiles: list[AnalysisProfile],
        resource_limits: Mapping[
            AnalysisResourceClass,
            ResourcePoolLimit,
        ],
        max_concurrent_media_processes: int,
        max_admitted_work: int = 2048,
        max_work_age_seconds: float = 120.0,
        max_segments_per_batch: int = 20,
        admission_queue: AdmissionQueue | None = None,
        metrics: RuntimeMetricCollector | None = None,
    ) -> None:
        if max_segments_per_batch <= 0:
            raise ValueError("max_segments_per_batch must be > 0")
        if max_concurrent_media_processes <= 0:
            raise ValueError("max_concurrent_media_processes must be > 0")
        self.stream = stream
        self.state_store = state_store
        self.profiles = {profile.name: profile for profile in analysis_profiles}
        if len(self.profiles) != len(analysis_profiles):
            raise ValueError("Analysis profile names must be unique")
        self.resource_class_by_profile = {
            profile.name: getattr(
                profile,
                "resource_class",
                AnalysisResourceClass.VIDEO_DECODE,
            )
            for profile in analysis_profiles
        }
        self.processors_by_profile: dict[str, list[SegmentProcessor]] = {
            name: [] for name in self.profiles
        }
        self._register_processors(processors)
        self.executors_by_resource = {}
        for resource_class in set(self.resource_class_by_profile.values()):
            try:
                limit = resource_limits[resource_class]
            except KeyError as exc:
                raise ValueError(
                    f"Missing resource limit for {resource_class.value}"
                ) from exc
            self.executors_by_resource[resource_class] = BoundedExecutor(
                limit.max_workers,
                limit.max_pending_tasks,
            )
        self.admission_queue = admission_queue or SegmentAdmissionQueue(
            max_items=max_admitted_work,
            max_age_seconds=max_work_age_seconds,
        )
        self.max_segments_per_batch = max_segments_per_batch
        self.media_process_gate = BoundedSemaphore(
            max_concurrent_media_processes
        )
        self.worker = ProfileWorkerCoordinator(
            state_store,
            metrics,
            media_process_gate=self.media_process_gate,
        )
        self.batch_lock = Lock()
        self.active_batches: set[tuple[str, str]] = set()
        self.dispatch_cursor = 0

    def _register_processors(self, processors: list[SegmentProcessor]) -> None:
        names: set[str] = set()
        for processor in processors:
            if processor.name in names:
                raise ValueError(
                    f"Processor names must be unique: {processor.name}"
                )
            names.add(processor.name)
            profile = self.profiles.get(processor.analysis_profile)
            if profile is None:
                raise ValueError(
                    f"Processor {processor.name!r} requires unknown "
                    f"analysis profile {processor.analysis_profile!r}"
                )
            if not processor.requirements.issubset(profile.provides):
                missing = processor.requirements.difference(profile.provides)
                raise ValueError(
                    f"Analysis profile {profile.name!r} does not provide "
                    f"requirements: {sorted(missing)}"
                )
            self.processors_by_profile[profile.name].append(processor)

    def shutdown(self, *, wait: bool = True) -> None:
        for executor in self.executors_by_resource.values():
            executor.shutdown(wait=wait)

    @property
    def executor(self) -> BoundedExecutor:
        return self.executors_by_resource[AnalysisResourceClass.VIDEO_DECODE]

    @executor.setter
    def executor(self, executor: BoundedExecutor) -> None:
        self.executors_by_resource[AnalysisResourceClass.VIDEO_DECODE] = executor

    def admit_snapshot(
        self,
        *,
        snapshot: MediaPlaylistSnapshot,
        stats: LiveCycleStats,
    ) -> None:
        segments = sorted(snapshot.segments, key=lambda item: item.sequence)
        for profile_name, profile in self.profiles.items():
            processors = self.processors_by_profile[profile_name]
            eligible = [
                segment
                for segment in segments
                if processors
                and profile.supports_segment(segment)
                and any(p.supports_segment(segment) for p in processors)
            ]
            result = self.admission_queue.admit(
                profile_name=profile_name,
                segments=eligible,
            )
            stats.admitted_work_count += result.admitted
            self._record_drops(stats, result.drops)
        self._record_queue_metrics(stats)

    def dispatch_pending(self, *, stats: LiveCycleStats) -> None:
        self._record_drops(stats, self.admission_queue.expire())
        groups: dict[tuple[str, str], list[AdmittedProfileSegment]] = {}
        for item in self.admission_queue.snapshot():
            key = (
                item.identity.profile_name,
                item.identity.variant_stable_id,
            )
            groups.setdefault(key, []).append(item)

        entries = list(groups.items())
        if entries:
            offset = self.dispatch_cursor % len(entries)
            entries = entries[offset:] + entries[:offset]
            self.dispatch_cursor = (offset + 1) % len(entries)
        for (profile_name, variant_id), items in entries:
            self._plan_group(
                profile=self.profiles[profile_name],
                variant_stable_id=variant_id,
                items=items,
                stats=stats,
            )
        self._record_queue_metrics(stats)

    def _plan_group(
        self,
        *,
        profile: AnalysisProfile,
        variant_stable_id: str,
        items: list[AdmittedProfileSegment],
        stats: LiveCycleStats,
    ) -> None:
        processors = self.processors_by_profile[profile.name]
        ordered = sorted(
            items,
            key=lambda item: (
                item.identity.timeline_generation,
                item.identity.discontinuity_sequence,
                item.identity.sequence,
                item.identity.media_revision,
            ),
        )
        identities = []
        candidates: dict[ProfileSegmentIdentity, list[ProcessorSegmentWork]] = {}
        for item in ordered:
            for processor in processors:
                if not processor.supports_segment(item.segment):
                    continue
                identity = self._processing_identity(processor, item)
                identities.append(identity)
                candidates.setdefault(item.identity, []).append(
                    ProcessorSegmentWork(processor, identity)
                )
        records = self.state_store.get_records(identities)
        completed = []
        work = []
        for item in ordered:
            pending = tuple(
                candidate
                for candidate in candidates.get(item.identity, [])
                if self._needs_processing(records[candidate.identity])
            )
            if pending:
                work.append(
                    ProfileSegmentWork(item.identity, item.segment, pending)
                )
            else:
                completed.append(item.identity)
        self.admission_queue.acknowledge(completed)
        if work:
            self._submit_batch(
                profile=profile,
                variant_stable_id=variant_stable_id,
                work=work[: self.max_segments_per_batch],
                stats=stats,
            )

    def _submit_batch(
        self,
        *,
        profile: AnalysisProfile,
        variant_stable_id: str,
        work: list[ProfileSegmentWork],
        stats: LiveCycleStats,
    ) -> None:
        batch_key = (profile.name, variant_stable_id)
        if not self._reserve_batch(batch_key):
            return
        admission_ids = tuple(item.admission_identity for item in work)
        self.admission_queue.protect(admission_ids)
        executor = self.executors_by_resource[
            self.resource_class_by_profile[profile.name]
        ]
        future = executor.try_submit(
            self._process_batch,
            profile,
            work,
            batch_key,
            admission_ids,
        )
        work_count = sum(len(item.processors) for item in work)
        if future is None:
            self._release_batch(batch_key)
            self.admission_queue.release(admission_ids)
            stats.backpressure_deferred_work_count += work_count
            return
        stats.scheduled_work_count += work_count

    def _process_batch(
        self,
        profile: AnalysisProfile,
        work: list[ProfileSegmentWork],
        batch_key: tuple[str, str],
        admission_ids: tuple[ProfileSegmentIdentity, ...],
    ) -> None:
        try:
            self.worker.process_batch(profile, work)
        finally:
            self.admission_queue.release(admission_ids)
            self._release_batch(batch_key)

    def _reserve_batch(self, batch_key: tuple[str, str]) -> bool:
        with self.batch_lock:
            if batch_key in self.active_batches:
                return False
            self.active_batches.add(batch_key)
            return True

    def _release_batch(self, batch_key: tuple[str, str]) -> None:
        with self.batch_lock:
            self.active_batches.discard(batch_key)

    def _processing_identity(
        self,
        processor: SegmentProcessor,
        item: AdmittedProfileSegment,
    ) -> SegmentProcessingIdentity:
        return SegmentProcessingIdentity(
            stream_id=self.stream.stream_id,
            check_name=processor.name,
            variant_stable_id=item.identity.variant_stable_id,
            discontinuity_sequence=(
                item.identity.discontinuity_sequence
            ),
            sequence=item.identity.sequence,
            timeline_generation=item.identity.timeline_generation,
            media_revision=item.identity.media_revision,
        )

    def _needs_processing(self, record: SegmentProcessingRecord) -> bool:
        if record.status is None:
            return True
        if record.status in (
            SegmentProcessingStatus.SUCCESS,
            SegmentProcessingStatus.FAILED_TERMINAL,
        ):
            return False
        return record.attempts < self.state_store.max_attempts

    @staticmethod
    def _record_drops(
        stats: LiveCycleStats,
        drops: tuple[AdmissionDrop, ...],
    ) -> None:
        for drop in drops:
            logger.warning(
                "Admission work dropped by policy profile=%s variant=%s "
                "seq=%s reason=%s",
                drop.identity.profile_name,
                drop.identity.variant_stable_id,
                drop.identity.sequence,
                drop.reason.value,
                extra={
                    "event_name": "admission_work_dropped",
                    "profile_name": drop.identity.profile_name,
                    "variant_stable_id": (
                        drop.identity.variant_stable_id
                    ),
                    "segment_sequence": drop.identity.sequence,
                    "drop_reason": drop.reason.value,
                },
            )
        stats.dropped_work_count += len(drops)
        stats.dropped_expired_work_count += sum(
            drop.reason == AdmissionDropReason.EXPIRED for drop in drops
        )
        stats.dropped_capacity_work_count += sum(
            drop.reason == AdmissionDropReason.CAPACITY for drop in drops
        )

    def _record_queue_metrics(self, stats: LiveCycleStats) -> None:
        stats.queue_depth = self.admission_queue.depth
        stats.queue_lag_seconds = self.admission_queue.oldest_age_seconds
