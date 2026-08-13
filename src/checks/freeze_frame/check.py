from dataclasses import dataclass

from core.context import MonitoringContext
from core.monitor import CheckResult
from detectors.freeze_frame import detect_freeze
from models.freeze import (
    FreezeEvent,
    FreezeInterval,
)
from models.segment import Segment
from playlist.master_parser import Variant


@dataclass
class FreezeFrameVariantResult:
    variant: Variant
    events: list[FreezeEvent]


def _build_segment_timeline(
    segments: list[Segment],
) -> list[tuple[Segment, float, float]]:

    timeline = []

    current_time = 0.0

    for segment in segments:

        start = current_time
        end = start + segment.duration

        timeline.append(
            (
                segment,
                start,
                end,
            )
        )

        current_time = end

    return timeline


def _map_interval_to_event(
    variant: Variant,
    interval: FreezeInterval,
    segments: list[Segment],
) -> FreezeEvent | None:

    timeline = _build_segment_timeline(
        segments
    )

    affected = []

    for segment, segment_start, segment_end in timeline:

        overlaps = (
            interval.start < segment_end
            and interval.end > segment_start
        )

        if overlaps:
            affected.append(
                (
                    segment,
                    segment_start,
                    segment_end,
                )
            )

    if not affected:
        return None

    first_segment, first_start, _ = affected[0]
    last_segment, last_start, _ = affected[-1]

    start_offset = (
        interval.start - first_start
    )

    end_offset = (
        interval.end - last_start
    )

    return FreezeEvent(
        variant_id=variant.id,

        start_time=interval.start,
        end_time=interval.end,
        duration=interval.duration,

        start_sequence=first_segment.sequence,
        end_sequence=last_segment.sequence,

        start_offset=start_offset,
        end_offset=end_offset,

        affected_segments=[
            segment.sequence
            for segment, _, _ in affected
        ],
    )


class FreezeFrameCheck:
    name = "freeze_frame"

    def __init__(
        self,
        noise: float = 0.003,
        min_duration: float = 2.0,
    ):
        self.noise = noise
        self.min_duration = min_duration

    def run(
        self,
        context: MonitoringContext,
    ) -> CheckResult:

        results = self.run_raw(
            context
        )

        return CheckResult(
            name=self.name,
            result=results,
            has_issue=any(
                variant_result.events
                for variant_result in results.values()
            ),
        )

    def run_raw(
        self,
        context: MonitoringContext,
    ) -> dict[str, FreezeFrameVariantResult]:

        results = {}

        for variant in context.variants:

            segments = context.segments_for_variant(
                variant
            )

            if not segments:
                results[variant.id] = (
                    FreezeFrameVariantResult(
                        variant=variant,
                        events=[],
                    )
                )

                continue

            total_duration = sum(
                segment.duration
                for segment in segments
            )

            intervals = detect_freeze(
                media_playlist_url=variant.uri,
                total_duration=total_duration,
                noise=self.noise,
                min_duration=self.min_duration,
            )

            events = []

            for interval in intervals:

                event = _map_interval_to_event(
                    variant=variant,
                    interval=interval,
                    segments=segments,
                )

                if event is not None:
                    events.append(event)

            results[variant.id] = (
                FreezeFrameVariantResult(
                    variant=variant,
                    events=events,
                )
            )

        return results
