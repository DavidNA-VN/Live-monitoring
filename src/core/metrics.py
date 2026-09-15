from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from collections.abc import Mapping

from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.audio import AudioTrackPresence, SilenceInterval
from models.freeze import FreezeInterval


@dataclass(frozen=True)
class AnalysisMetricSnapshot:
    analysis_count: int
    analysis_duration_seconds_total: float
    segment_age_seconds_max: float
    retry_total: int
    ffmpeg_timeout_total: int
    profile_metrics: Mapping[str, int | float]
    audio_analysis_total: int
    audio_analysis_failure_total: int
    audio_analysis_timeout_total: int
    audio_track_missing_total: int
    audio_silence_seconds_total: float


class RuntimeMetricCollector:
    """Thread-safe bridge from async profile workers to cycle metrics."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._analysis_count = 0
        self._analysis_duration = 0.0
        self._segment_age_max = 0.0
        self._retry_total = 0
        self._ffmpeg_timeout_total = 0
        self._profile_metrics: dict[str, int | float] = {}
        self._duration_samples: dict[str, list[float]] = {}
        self._audio_analysis_total = 0
        self._audio_analysis_failure_total = 0
        self._audio_analysis_timeout_total = 0
        self._audio_track_missing_total = 0
        self._audio_silence_seconds_total = 0.0

    def record_analysis(
        self,
        *,
        duration_seconds: float,
        segment_age_seconds: float,
        ffmpeg_timed_out: bool,
    ) -> None:
        with self._lock:
            self._analysis_count += 1
            self._analysis_duration += duration_seconds
            self._segment_age_max = max(
                self._segment_age_max, segment_age_seconds
            )
            self._ffmpeg_timeout_total += int(ffmpeg_timed_out)

    def record_profile_result(self, analysis: SegmentAnalysisBundle) -> None:
        video = analysis.video_realtime
        if video is not None:
            intervals = video.outputs.get(
                AnalysisRequirement.FREEZE_INTERVALS, ()
            )
            freeze_intervals = tuple(
                interval
                for interval in intervals
                if isinstance(interval, FreezeInterval)
            ) if isinstance(intervals, tuple) else ()
            with self._lock:
                self._increment_profile_metric("video_analysis_total", 1)
                self._increment_profile_metric(
                    "video_analysis_failure_total", int(not video.checked)
                )
                self._increment_profile_metric(
                    "video_analysis_timeout_total", int(video.timed_out)
                )
                self._increment_profile_metric(
                    "video_freeze_interval_total", len(freeze_intervals)
                )
                self._increment_profile_metric(
                    "video_freeze_seconds_total",
                    sum(interval.duration for interval in freeze_intervals),
                )
                for name in (
                    "video_freeze_raw_interval_total",
                    "video_freeze_raw_seconds_total",
                    "video_freeze_black_overlap_seconds_total",
                    "video_freeze_boundary_fingerprint_total",
                ):
                    if name not in video.diagnostics:
                        continue
                    value = video.diagnostics[name]
                    if isinstance(value, (int, float)) and not isinstance(
                        value, bool
                    ):
                        self._increment_profile_metric(name, value)

        audio = analysis.audio_realtime
        if audio is None:
            return
        silence_seconds = 0.0
        intervals = audio.outputs.get(AnalysisRequirement.SILENCE_INTERVALS, ())
        for interval in intervals if isinstance(intervals, tuple) else ():
            if isinstance(interval, SilenceInterval):
                silence_seconds += max(0.0, interval.end - interval.start)
        with self._lock:
            self._audio_analysis_total += 1
            self._audio_analysis_failure_total += int(not audio.checked)
            self._audio_analysis_timeout_total += int(audio.timed_out)
            self._audio_track_missing_total += int(
                audio.checked
                and audio.presence is AudioTrackPresence.ABSENT
            )
            self._audio_silence_seconds_total += silence_seconds

    def record_retry(self) -> None:
        with self._lock:
            self._retry_total += 1

    def record_work_submitted(self, profile_name: str, count: int) -> None:
        self._record_count(profile_name, "work_submitted_total", count)

    def record_work_started(
        self,
        profile_name: str,
        *,
        count: int,
        executor_wait_seconds: float,
    ) -> None:
        with self._lock:
            self._increment_profile_metric(
                self._name(profile_name, "work_started_total"), count
            )
            self._record_duration_locked(
                profile_name,
                "executor_wait_seconds",
                executor_wait_seconds,
                count=count,
            )

    def record_process_gate_wait(
        self, profile_name: str, duration_seconds: float
    ) -> None:
        self._record_duration(
            profile_name, "process_gate_wait_seconds", duration_seconds
        )

    def record_profile_execution(
        self, profile_name: str, duration_seconds: float
    ) -> None:
        self._record_duration(
            profile_name, "profile_execution_seconds", duration_seconds
        )

    def record_result_processing(
        self, profile_name: str, duration_seconds: float
    ) -> None:
        self._record_duration(
            profile_name, "result_processing_seconds", duration_seconds
        )

    def record_result_commit(
        self, profile_name: str, duration_seconds: float
    ) -> None:
        self._record_duration(
            profile_name, "result_commit_seconds", duration_seconds
        )

    def record_work_completed(self, profile_name: str) -> None:
        self._record_count(profile_name, "work_completed_total", 1)

    def record_work_failed(self, profile_name: str) -> None:
        self._record_count(profile_name, "work_failed_total", 1)

    def record_work_timed_out(self, profile_name: str) -> None:
        self._record_count(profile_name, "work_timed_out_total", 1)

    def _record_count(self, profile_name: str, metric: str, count: int) -> None:
        if count < 0:
            raise ValueError("metric count must be >= 0")
        with self._lock:
            self._increment_profile_metric(self._name(profile_name, metric), count)

    def _record_duration(
        self, profile_name: str, metric: str, duration_seconds: float
    ) -> None:
        if duration_seconds < 0:
            raise ValueError("metric duration must be >= 0")
        with self._lock:
            self._record_duration_locked(
                profile_name, metric, duration_seconds, count=1
            )

    def _record_duration_locked(
        self,
        profile_name: str,
        metric: str,
        duration_seconds: float,
        *,
        count: int,
    ) -> None:
        self._increment_profile_metric(
            self._name(profile_name, f"{metric}_total"),
            duration_seconds * count,
        )
        maximum = self._name(profile_name, f"{metric}_max")
        self._profile_metrics[maximum] = max(
            self._profile_metrics.get(maximum, 0.0), duration_seconds
        )
        samples = self._duration_samples.setdefault(
            self._name(profile_name, metric), []
        )
        if len(samples) < 2048:
            samples.append(duration_seconds)

    @staticmethod
    def _name(profile_name: str, metric: str) -> str:
        return f"perf_{profile_name}_{metric}"

    def drain(self) -> AnalysisMetricSnapshot:
        with self._lock:
            profile_metrics = dict(self._profile_metrics)
            for name, samples in self._duration_samples.items():
                ordered = sorted(samples)
                for percentile in (50, 95, 99):
                    index = max(
                        0,
                        min(
                            len(ordered) - 1,
                            (len(ordered) * percentile + 99) // 100 - 1,
                        ),
                    )
                    profile_metrics[f"{name}_p{percentile}"] = ordered[index]
            snapshot = AnalysisMetricSnapshot(
                analysis_count=self._analysis_count,
                analysis_duration_seconds_total=self._analysis_duration,
                segment_age_seconds_max=self._segment_age_max,
                retry_total=self._retry_total,
                ffmpeg_timeout_total=self._ffmpeg_timeout_total,
                profile_metrics=MappingProxyType(profile_metrics),
                audio_analysis_total=self._audio_analysis_total,
                audio_analysis_failure_total=(
                    self._audio_analysis_failure_total
                ),
                audio_analysis_timeout_total=(
                    self._audio_analysis_timeout_total
                ),
                audio_track_missing_total=self._audio_track_missing_total,
                audio_silence_seconds_total=(
                    self._audio_silence_seconds_total
                ),
            )
            self._analysis_count = 0
            self._analysis_duration = 0.0
            self._segment_age_max = 0.0
            self._retry_total = 0
            self._ffmpeg_timeout_total = 0
            self._profile_metrics = {}
            self._duration_samples = {}
            self._audio_analysis_total = 0
            self._audio_analysis_failure_total = 0
            self._audio_analysis_timeout_total = 0
            self._audio_track_missing_total = 0
            self._audio_silence_seconds_total = 0.0
            return snapshot

    def _increment_profile_metric(self, name: str, value: int | float) -> None:
        self._profile_metrics[name] = self._profile_metrics.get(name, 0) + value
