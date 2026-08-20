from dataclasses import dataclass
from typing import Protocol

from models.segment import Segment
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
)


@dataclass(frozen=True)
class SegmentProcessOutcome:
    success: bool
    retryable: bool = False
    error: str | None = None
    payload: object | None = None

    @classmethod
    def ok(
        cls,
        payload: object | None = None,
    ) -> "SegmentProcessOutcome":
        return cls(
            success=True,
            payload=payload,
        )

    @classmethod
    def retry(
        cls,
        error: str,
    ) -> "SegmentProcessOutcome":
        return cls(
            success=False,
            retryable=True,
            error=error,
        )

    @classmethod
    def terminal(
        cls,
        error: str,
    ) -> "SegmentProcessOutcome":
        return cls(
            success=False,
            retryable=False,
            error=error,
        )


class SegmentProcessor(Protocol):
    name: str
    analysis_profile: str
    requirements: frozenset[AnalysisRequirement]

    def supports_segment(
        self,
        segment: Segment,
    ) -> bool:
        ...

    def process(
        self,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> SegmentProcessOutcome:
        ...

    def commit(
        self,
        segment: Segment,
        outcome: SegmentProcessOutcome,
    ) -> None:
        ...
