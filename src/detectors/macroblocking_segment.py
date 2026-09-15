from __future__ import annotations

import math

from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.macroblocking import (
    MacroblockingDetectionResult,
    MacroblockingFrameObservation,
    MacroblockingInterval,
)
from models.segment import Segment
from policies.macroblocking import (
    MacroblockingAlertPolicy,
    MacroblockingCandidateRule,
)


class MacroblockingDetector:
    def __init__(
        self,
        *,
        sampling_fps: float = 1.0,
        candidate_rule: MacroblockingCandidateRule | None = None,
    ) -> None:
        if not math.isfinite(sampling_fps) or sampling_fps <= 0:
            raise ValueError("sampling_fps must be finite and > 0")
        self.sampling_fps = sampling_fps
        self.sample_duration = 1.0 / sampling_fps
        self.candidate_rule = candidate_rule or MacroblockingCandidateRule(
            alert_policy=MacroblockingAlertPolicy(
                affected_area_threshold=0.15,
            ),
            detector_confidence_threshold=0.65,
        )

    def detect(
        self,
        *,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> MacroblockingDetectionResult:
        video = analysis.require_video_realtime()
        if not video.checked:
            return self._result(
                segment,
                checked=False,
                error=video.error or "video analysis failed",
                retryable=video.retryable,
                coverage_complete=False,
            )

        observations = video.require_output(
            AnalysisRequirement.MACROBLOCKING_OBSERVATIONS,
            tuple,
        )
        if not all(
            isinstance(item, MacroblockingFrameObservation)
            for item in observations
        ):
            raise TypeError("Invalid macroblocking_observations analysis output")
        maximum_observations = math.ceil(
            segment.duration * self.sampling_fps
        ) + 2
        if len(observations) > maximum_observations:
            return self._result(
                segment,
                checked=False,
                error="macroblocking observation count exceeds segment bound",
                retryable=False,
                coverage_complete=False,
            )
        if not observations:
            return self._result(
                segment,
                checked=False,
                error="macroblocking analysis produced no observations",
                coverage_complete=False,
            )
        if any(
            current.offset_seconds <= previous.offset_seconds
            for previous, current in zip(observations, observations[1:])
        ):
            return self._result(
                segment,
                checked=False,
                error="macroblocking observations are not strictly ordered",
                retryable=False,
                coverage_complete=False,
            )

        invalid_count = sum(not item.valid for item in observations)
        expected_minimum = max(1, math.ceil(segment.duration * self.sampling_fps))
        cadence_complete = (
            observations[0].offset_seconds <= self.sample_duration * 0.5
            and all(
                current.offset_seconds - previous.offset_seconds
                <= self.sample_duration * 1.5
                for previous, current in zip(observations, observations[1:])
            )
            and observations[-1].offset_seconds + self.sample_duration
            >= segment.duration - self.sample_duration * 0.5
        )
        coverage_complete = (
            invalid_count == 0
            and len(observations) >= expected_minimum
            and cadence_complete
        )
        intervals = self._aggregate_intervals(
            observations,
            segment_duration=segment.duration,
        )
        return self._result(
            segment,
            observations=observations,
            intervals=intervals,
            coverage_complete=coverage_complete,
            invalid_observation_count=invalid_count,
        )

    def _aggregate_intervals(
        self,
        observations: tuple[MacroblockingFrameObservation, ...],
        *,
        segment_duration: float,
    ) -> tuple[MacroblockingInterval, ...]:
        intervals: list[MacroblockingInterval] = []
        current: list[tuple[MacroblockingFrameObservation, float]] = []
        for index, observation in enumerate(observations):
            if (
                index > 0
                and observation.offset_seconds
                - observations[index - 1].offset_seconds
                > self.sample_duration * 1.5
                and current
            ):
                intervals.append(self._interval(current))
                current = []
            next_offset = (
                observations[index + 1].offset_seconds
                if index + 1 < len(observations)
                else segment_duration
            )
            end = min(
                segment_duration,
                observation.offset_seconds + self.sample_duration,
                next_offset,
            )
            duration = max(0.0, end - observation.offset_seconds)
            if (
                duration > 0
                and observation.valid
                and self.candidate_rule.is_candidate(observation)
            ):
                current.append((observation, duration))
                continue
            if current:
                intervals.append(self._interval(current))
                current = []
        if current:
            intervals.append(self._interval(current))
        return tuple(intervals)

    @staticmethod
    def _interval(
        samples: list[tuple[MacroblockingFrameObservation, float]],
    ) -> MacroblockingInterval:
        total_duration = sum(duration for _, duration in samples)

        def weighted(attribute: str) -> float:
            return sum(
                getattr(observation, attribute) * duration
                for observation, duration in samples
            ) / total_duration

        observations = [item for item, _ in samples]
        return MacroblockingInterval(
            start=observations[0].offset_seconds,
            end=observations[-1].offset_seconds + samples[-1][1],
            average_affected_area_ratio=weighted("affected_area_ratio"),
            peak_affected_area_ratio=max(
                item.affected_area_ratio for item in observations
            ),
            average_blocking_confidence=weighted("blocking_confidence"),
            peak_blocking_confidence=max(
                item.blocking_confidence for item in observations
            ),
            average_boundary_support_ratio=weighted(
                "boundary_support_ratio"
            ),
        )

    @staticmethod
    def _result(
        segment: Segment,
        *,
        checked: bool = True,
        error: str | None = None,
        retryable: bool = True,
        observations: tuple[MacroblockingFrameObservation, ...] = (),
        intervals: tuple[MacroblockingInterval, ...] = (),
        coverage_complete: bool = True,
        invalid_observation_count: int = 0,
    ) -> MacroblockingDetectionResult:
        return MacroblockingDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            observations=observations,
            intervals=intervals,
            program_date_time=segment.program_date_time,
            checked=checked,
            error=error,
            retryable=retryable,
            coverage_complete=coverage_complete,
            invalid_observation_count=invalid_observation_count,
        )
