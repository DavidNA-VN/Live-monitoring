from __future__ import annotations

import json
import statistics
import subprocess
from typing import Any

from checks.audio_loss.models import (
    AudioPacket,
    AudioPacketAnalysis,
    AudioPacketGap,
)
from models.segment import Segment


class AudioPacketAnalyzer:

    def __init__(
        self,
        gap_multiplier: float = 1.5,
        timeout: int = 30,
    ):
        self.gap_multiplier = gap_multiplier
        self.timeout = timeout

    def analyze(
        self,
        segments: list[Segment],
    ) -> list[AudioPacketAnalysis]:

        return [
            self.analyze_segment(segment)
            for segment in segments
        ]

    def analyze_segment(
        self,
        segment: Segment,
    ) -> AudioPacketAnalysis:

        packets, error = self._probe_packets(
            segment.uri
        )

        if error is not None:
            return AudioPacketAnalysis(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                packet_count=0,
                checked=False,
                error=error,
            )

        if len(packets) < 2:
            return AudioPacketAnalysis(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                packet_count=len(packets),
            )

        expected = self._expected_duration(
            packets
        )

        if expected is None or expected <= 0:
            return AudioPacketAnalysis(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                packet_count=len(packets),
            )

        gaps = []

        for previous, current in zip(
            packets,
            packets[1:],
        ):
            actual_gap = current.pts - previous.pts

            if actual_gap <= expected * self.gap_multiplier:
                continue

            gaps.append(
                AudioPacketGap(
                    sequence=segment.sequence,
                    previous_pts=previous.pts,
                    current_pts=current.pts,
                    expected_duration=expected,
                    actual_gap=actual_gap,
                    estimated_missing_packets=max(
                        1,
                        round(actual_gap / expected) - 1,
                    ),
                )
            )

        return AudioPacketAnalysis(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            packet_count=len(packets),
            expected_packet_duration=expected,
            gaps=gaps,
        )

    def _probe_packets(
        self,
        uri: str,
    ) -> tuple[list[AudioPacket], str | None]:

        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=pts_time,duration_time,size",
            "-of",
            "json",
            uri,
        ]

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return (
                [],
                f"ffprobe timeout after {exc.timeout}s",
            )

        if process.returncode != 0:
            return (
                [],
                process.stderr.strip(),
            )

        try:
            payload: dict[str, Any] = json.loads(
                process.stdout or "{}"
            )
        except json.JSONDecodeError as exc:
            return (
                [],
                f"Cannot parse ffprobe JSON: {exc}",
            )

        packets = []

        for packet in payload.get(
            "packets",
            [],
        ):
            pts = self._parse_float(
                packet.get("pts_time")
            )

            if pts is None:
                continue

            packets.append(
                AudioPacket(
                    pts=pts,
                    duration=self._parse_float(
                        packet.get("duration_time")
                    ),
                    size=self._parse_int(
                        packet.get("size")
                    ),
                )
            )

        packets.sort(
            key=lambda packet: packet.pts
        )

        return packets, None

    @staticmethod
    def _expected_duration(
        packets: list[AudioPacket],
    ) -> float | None:

        durations = [
            packet.duration
            for packet in packets
            if (
                packet.duration is not None
                and packet.duration > 0
            )
        ]

        if durations:
            return statistics.median(
                durations
            )

        pts_gaps = [
            current.pts - previous.pts
            for previous, current in zip(
                packets,
                packets[1:],
            )
            if current.pts > previous.pts
        ]

        if not pts_gaps:
            return None

        return statistics.median(
            pts_gaps
        )

    @staticmethod
    def _parse_float(
        value,
    ) -> float | None:

        if value in (
            None,
            "N/A",
        ):
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_int(
        value,
    ) -> int | None:

        if value in (
            None,
            "N/A",
        ):
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None
