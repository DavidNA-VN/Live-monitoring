from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VariantSelectionMode(str, Enum):
    ALL = "all"
    REPRESENTATIVE = "representative"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class VariantSelectionPolicy:
    mode: VariantSelectionMode = VariantSelectionMode.ALL
    representative_count: int = 3
    explicit_variant_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", VariantSelectionMode(self.mode))
        normalized = tuple(
            dict.fromkeys(value.strip() for value in self.explicit_variant_ids)
        )
        if any(not value for value in normalized):
            raise ValueError("explicit_variant_ids must not contain empty IDs")
        object.__setattr__(self, "explicit_variant_ids", normalized)
        if self.representative_count <= 0:
            raise ValueError("representative_count must be > 0")
        if self.mode is VariantSelectionMode.EXPLICIT and not normalized:
            raise ValueError("explicit mode requires explicit_variant_ids")
        if self.mode is not VariantSelectionMode.EXPLICIT and normalized:
            raise ValueError("explicit_variant_ids require explicit mode")
