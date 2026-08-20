from dataclasses import dataclass
from enum import Enum


class EvidenceStrength(
    str,
    Enum,
):
    STRONG = "strong"
    SUPPORTING = "supporting"
    INFORMATIONAL = "informational"


@dataclass(frozen=True)
class EventEvidence:
    evidence_type: str
    strength: EvidenceStrength
    source: str
    detail: str

@dataclass(frozen=True)
class CrossVariantEventEvidence:
    checked: bool

    analyzed_variant_count: int

    overlapping_variant_ids: list[str]

    reason: str | None = None