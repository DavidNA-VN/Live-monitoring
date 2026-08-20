from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class AnalysisMetricSnapshot:
    analysis_count: int
    analysis_duration_seconds_total: float
    segment_age_seconds_max: float
    retry_total: int
    ffmpeg_timeout_total: int


class RuntimeMetricCollector:
    """Thread-safe bridge from async profile workers to cycle metrics."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._analysis_count = 0
        self._analysis_duration = 0.0
        self._segment_age_max = 0.0
        self._retry_total = 0
        self._ffmpeg_timeout_total = 0

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

    def record_retry(self) -> None:
        with self._lock:
            self._retry_total += 1

    def drain(self) -> AnalysisMetricSnapshot:
        with self._lock:
            snapshot = AnalysisMetricSnapshot(
                analysis_count=self._analysis_count,
                analysis_duration_seconds_total=self._analysis_duration,
                segment_age_seconds_max=self._segment_age_max,
                retry_total=self._retry_total,
                ffmpeg_timeout_total=self._ffmpeg_timeout_total,
            )
            self._analysis_count = 0
            self._analysis_duration = 0.0
            self._segment_age_max = 0.0
            self._retry_total = 0
            self._ffmpeg_timeout_total = 0
            return snapshot
