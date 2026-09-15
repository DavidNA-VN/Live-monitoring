from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundaryFrameFingerprint:
    """Small, versioned luminance sample used at media boundaries."""

    algorithm: str
    width: int
    height: int
    pixels: bytes
    is_black: bool

    def __post_init__(self) -> None:
        if not self.algorithm:
            raise ValueError("fingerprint algorithm must not be empty")
        if (
            isinstance(self.width, bool)
            or not isinstance(self.width, int)
            or isinstance(self.height, bool)
            or not isinstance(self.height, int)
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError("fingerprint dimensions must be > 0")
        if not isinstance(self.is_black, bool):
            raise TypeError("fingerprint is_black must be a boolean")
        expected = self.width * self.height
        if len(self.pixels) != expected:
            raise ValueError(
                "fingerprint pixel length does not match its dimensions"
            )
