from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import BinaryIO, Iterator

import numpy as np

from detectors.macroblocking import MacroblockingAnalyzer
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from policies.macroblocking import (
    MacroblockingAlertPolicy,
    MacroblockingCandidateRule,
)
from scripts.benchmark_macroblocking import SCHEMA_VERSION, load_manifest


def iter_luminance_frames(
    source: Path | str,
    *,
    width: int,
    height: int,
    sampling_fps: float,
    ffmpeg: str = "ffmpeg",
) -> Iterator[np.ndarray]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        f"fps={sampling_fps:g},scale={width}:{height}:flags=area,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = width * height
    try:
        while payload := _read_exact(process.stdout, frame_bytes):
            yield np.frombuffer(payload, dtype=np.uint8).reshape(height, width)
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait(timeout=10)
    if return_code != 0:
        raise RuntimeError(stderr.strip() or "FFmpeg frame extraction failed")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if payload and len(payload) != size:
        raise RuntimeError("FFmpeg produced a truncated raw video frame")
    return payload


def current_working_set_bytes() -> int:
    if os.name != "nt":
        return 0

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    query = ctypes.windll.kernel32.K32GetProcessMemoryInfo
    query.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    query.restype = ctypes.c_int
    succeeded = query(
        handle,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.working_set_size) if succeeded else 0


def analyze_manifest(
    manifest_path: Path,
    *,
    config: MacroblockingAnalyzerConfig,
    area_threshold: float,
    ffmpeg: str = "ffmpeg",
) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    analyzer = MacroblockingAnalyzer(config)
    candidate_rule = MacroblockingCandidateRule(
        alert_policy=MacroblockingAlertPolicy(
            affected_area_threshold=area_threshold,
        ),
        detector_confidence_threshold=config.detector_confidence_threshold,
    )
    observations: list[dict[str, object]] = []
    frame_count = 0
    peak_bytes = current_working_set_bytes()
    started = time.perf_counter()
    for fixture in manifest["fixtures"]:
        source = manifest_path.parent / fixture["playlist"]
        for index, frame in enumerate(
            iter_luminance_frames(
                source,
                width=config.analysis_width,
                height=config.analysis_height,
                sampling_fps=config.sampling_fps,
                ffmpeg=ffmpeg,
            )
        ):
            timestamp = index / config.sampling_fps
            observation = analyzer.analyze(frame, offset_seconds=timestamp)
            assert not isinstance(observation, tuple)
            observations.append(
                {
                    "fixture": fixture["name"],
                    "timestamp_seconds": timestamp,
                    "candidate": candidate_rule.is_candidate(observation),
                    "affected_area_ratio": observation.affected_area_ratio,
                    "blocking_confidence": observation.blocking_confidence,
                    "boundary_support_ratio": observation.boundary_support_ratio,
                    "valid": observation.valid,
                }
            )
            frame_count += 1
            peak_bytes = max(peak_bytes, current_working_set_bytes())
    wall_seconds = time.perf_counter() - started
    return {
        "schema_version": SCHEMA_VERSION,
        "configuration": {
            "analysis_width": config.analysis_width,
            "analysis_height": config.analysis_height,
            "sampling_fps": config.sampling_fps,
            "relative_scale_divisors": list(config.relative_scale_divisors),
            "stride_ratio": config.stride_ratio,
            "fusion_strategy": config.fusion_strategy.value,
            "detector_confidence_threshold": config.detector_confidence_threshold,
            "area_threshold": area_threshold,
        },
        "observations": observations,
        "performance": {
            "frame_count": frame_count,
            "wall_seconds": wall_seconds,
            "peak_bytes": peak_bytes,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pure macroblocking analyzer")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fusion",
        choices=[item.value for item in MacroblockingFusionStrategy],
        required=True,
    )
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=270)
    parser.add_argument("--sampling-fps", type=float, default=1.0)
    parser.add_argument("--area-threshold", type=float, default=0.15)
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        parser.error("FFmpeg is not installed or not on PATH")
    config = MacroblockingAnalyzerConfig(
        fusion_strategy=MacroblockingFusionStrategy(args.fusion),
        analysis_width=args.width,
        analysis_height=args.height,
        sampling_fps=args.sampling_fps,
    )
    result = analyze_manifest(
        args.manifest,
        config=config,
        area_threshold=args.area_threshold,
        ffmpeg=ffmpeg,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
