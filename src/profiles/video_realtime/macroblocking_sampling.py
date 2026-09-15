from __future__ import annotations

from contextlib import AbstractContextManager
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from detectors.macroblocking import MacroblockingAnalyzer
from models.macroblocking import MacroblockingFrameObservation


class MacroblockingSampleWorkspace(AbstractContextManager):
    def __init__(self) -> None:
        self._temporary = TemporaryDirectory(prefix="media-monitor-macroblocking-")
        self.path = Path(self._temporary.name)
        self.raw_path = self.path / "luminance.raw"

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._temporary.cleanup()


def read_macroblocking_observations(
    raw_path: Path,
    *,
    analyzer: MacroblockingAnalyzer,
    width: int,
    height: int,
    sampling_fps: float,
    segment_duration: float,
) -> tuple[MacroblockingFrameObservation, ...]:
    if width <= 0 or height <= 0:
        raise ValueError("macroblocking sample dimensions must be > 0")
    if not math.isfinite(sampling_fps) or sampling_fps <= 0:
        raise ValueError("macroblocking sampling_fps must be finite and > 0")
    if not math.isfinite(segment_duration) or segment_duration <= 0:
        raise ValueError("segment_duration must be finite and > 0")

    frame_size = width * height
    size = raw_path.stat().st_size
    maximum_frames = math.ceil(segment_duration * sampling_fps) + 2
    maximum_bytes = maximum_frames * frame_size
    if size == 0:
        raise ValueError("raw macroblocking sample is empty")
    if size % frame_size:
        raise ValueError("raw macroblocking sample is truncated")
    if size > maximum_bytes:
        raise ValueError("raw macroblocking sample exceeds bounded frame count")

    observations: list[MacroblockingFrameObservation] = []
    with raw_path.open("rb") as stream:
        for index in range(size // frame_size):
            payload = stream.read(frame_size)
            if len(payload) != frame_size:
                raise ValueError("raw macroblocking sample is truncated")
            frame = np.frombuffer(payload, dtype=np.uint8).reshape(height, width)
            offset_seconds = index / sampling_fps
            if offset_seconds >= segment_duration:
                continue
            observation = analyzer.analyze(
                frame,
                offset_seconds=offset_seconds,
            )
            if isinstance(observation, tuple):
                raise TypeError("production analyzer must not return debug output")
            observations.append(observation)
    return tuple(observations)
