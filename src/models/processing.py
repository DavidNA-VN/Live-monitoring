from dataclasses import dataclass
from enum import Enum


class SegmentProcessingStatus(
    str,
    Enum,
):
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class SegmentClaimStatus(
    str,
    Enum,
):
    ACQUIRED = "acquired"
    BUSY = "busy"
    ALREADY_SUCCESSFUL = "already_successful"
    RETRY_EXHAUSTED = "retry_exhausted"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True)
class SegmentProcessingIdentity:
    stream_id: str
    check_name: str
    variant_stable_id: str
    timeline_generation: int
    discontinuity_sequence: int
    sequence: int
    media_revision: str


@dataclass(frozen=True)
class SegmentClaim:
    identity: SegmentProcessingIdentity
    status: SegmentClaimStatus

    lease_token: str | None = None
    attempt: int = 0

    @property
    def acquired(self) -> bool:
        return (
            self.status
            == SegmentClaimStatus.ACQUIRED
        )

@dataclass(frozen=True)
class SegmentProcessingRecord:
    identity: SegmentProcessingIdentity

    status: SegmentProcessingStatus | None

    attempts: int = 0

    updated_at: str | None = None
    last_error: str | None = None
