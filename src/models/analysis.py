from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TypeVar


class AnalysisRequirement(str, Enum):
    BLACK_INTERVALS = "black_intervals"


class AnalysisResourceClass(str, Enum):
    METADATA = "metadata"
    VIDEO_DECODE = "video_decode"
    AUDIO_DECODE = "audio_decode"
    EXPENSIVE = "expensive"


@dataclass(frozen=True)
class ResourcePoolLimit:
    max_workers: int
    max_pending_tasks: int

    def __post_init__(self) -> None:
        if self.max_workers <= 0:
            raise ValueError("max_workers must be > 0")
        if self.max_pending_tasks < 0:
            raise ValueError("max_pending_tasks must be >= 0")


def default_resource_limits() -> dict[
    AnalysisResourceClass,
    ResourcePoolLimit,
]:
    return {
        AnalysisResourceClass.METADATA: ResourcePoolLimit(2, 8),
        AnalysisResourceClass.VIDEO_DECODE: ResourcePoolLimit(4, 16),
        AnalysisResourceClass.AUDIO_DECODE: ResourcePoolLimit(1, 4),
        AnalysisResourceClass.EXPENSIVE: ResourcePoolLimit(1, 1),
    }


OutputT = TypeVar("OutputT")


@dataclass(frozen=True)
class VideoRealtimeAnalysis:
    checked: bool
    error: str | None = None
    retryable: bool = True
    timed_out: bool = False
    outputs: Mapping[AnalysisRequirement, object] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))

    def require_output(
        self,
        requirement: AnalysisRequirement,
        output_type: type[OutputT],
    ) -> OutputT:
        try:
            output = self.outputs[requirement]
        except KeyError as exc:
            raise ValueError(
                f"Video analysis has no {requirement.value} output"
            ) from exc
        if not isinstance(output, output_type):
            raise TypeError(
                f"Invalid {requirement.value} output: "
                f"expected {output_type.__name__}"
            )
        return output


@dataclass(frozen=True)
class SegmentAnalysisBundle:
    profile_name: str
    video_realtime: VideoRealtimeAnalysis | None = None

    def require_video_realtime(self) -> VideoRealtimeAnalysis:
        if self.video_realtime is None:
            raise ValueError(
                "Analysis bundle has no video_realtime result"
            )

        return self.video_realtime
