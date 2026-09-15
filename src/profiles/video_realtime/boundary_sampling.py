from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from tempfile import TemporaryDirectory

from models.frame_fingerprint import BoundaryFrameFingerprint
from core.frame_similarity import make_gray_fingerprint
from core.frame_similarity import (
    FINGERPRINT_HEIGHT,
    FINGERPRINT_WIDTH,
)


class BoundarySampleWorkspace(AbstractContextManager):
    """Owns the temporary raw output used by the Phase 0 FFmpeg spike."""

    def __init__(self) -> None:
        self._temporary = TemporaryDirectory(prefix="media-monitor-boundary-")
        self.path = Path(self._temporary.name)
        self.raw_path = self.path / "gray32.raw"

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._temporary.cleanup()


def build_boundary_sampling_spike_command(
    *,
    input_uri: str,
    raw_output_path: Path,
    filter_expressions: tuple[str, ...],
    width: int = FINGERPRINT_WIDTH,
    height: int = FINGERPRINT_HEIGHT,
) -> list[str]:
    if not input_uri:
        raise ValueError("input_uri must not be empty")
    if not filter_expressions:
        raise ValueError("at least one analysis filter is required")
    if width <= 0 or height <= 0:
        raise ValueError("sample dimensions must be > 0")
    if width != FINGERPRINT_WIDTH or height != FINGERPRINT_HEIGHT:
        raise ValueError("gray32-mae-v1 sampling must be 32x32")

    analysis_filters = ",".join(filter_expressions)
    graph = (
        f"[0:v:0]split=2[analysis][sample_source];"
        f"[analysis]{analysis_filters}[analyzed];"
        f"[sample_source]scale={width}:{height},format=gray[samples]"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "info",
        "-y",
        "-i",
        input_uri,
        "-filter_complex",
        graph,
        "-map",
        "[analyzed]",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
        "-map",
        "[samples]",
        "-an",
        "-sn",
        "-dn",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        str(raw_output_path),
    ]


def read_boundary_fingerprints(
    raw_path: Path,
    *,
    width: int = FINGERPRINT_WIDTH,
    height: int = FINGERPRINT_HEIGHT,
) -> tuple[BoundaryFrameFingerprint, BoundaryFrameFingerprint]:
    if width <= 0 or height <= 0:
        raise ValueError("sample dimensions must be > 0")
    if width != FINGERPRINT_WIDTH or height != FINGERPRINT_HEIGHT:
        raise ValueError("gray32-mae-v1 sampling must be 32x32")
    raw = raw_path.read_bytes()
    frame_size = width * height
    if not raw or len(raw) % frame_size:
        raise ValueError("raw boundary sample is empty or truncated")
    return (
        make_gray_fingerprint(raw[:frame_size], width=width, height=height),
        make_gray_fingerprint(raw[-frame_size:], width=width, height=height),
    )
