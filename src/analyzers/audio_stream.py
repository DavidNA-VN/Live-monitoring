from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from checks.audio_loss.models import (
    AudioStreamAnalysis,
    AudioStreamInfo,
)
from models.segment import Segment


TIMESTAMP_ERROR_PATTERN = re.compile(
    "|".join(
        [
            r"timestamp",
            r"non[- ]monoton",
            r"\bpts\b",
            r"\bdts\b",
            r"discontinuity",
        ]
    ),
    re.IGNORECASE,
)


class AudioStreamAnalyzer:

    def __init__(
        self,
        timeout: int = 30,
    ):
        self.timeout = timeout

    def analyze(
        self,
        segments: list[Segment],
    ) -> list[AudioStreamAnalysis]:

        return [
            self.analyze_segment(segment)
            for segment in segments
        ]

    def analyze_segment(
        self,
        segment: Segment,
    ) -> AudioStreamAnalysis:

        stream_infos, probe_error = self._probe_audio_streams(
            segment.uri
        )

        if probe_error is not None:
            return AudioStreamAnalysis(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                has_audio_stream=False,
                decodable=False,
                decode_errors=[
                    probe_error
                ],
            )

        if not stream_infos:
            return AudioStreamAnalysis(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                has_audio_stream=False,
                decodable=False,
            )

        decode_errors, timestamp_errors = self._decode_audio(
            segment.uri
        )

        return AudioStreamAnalysis(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            has_audio_stream=True,
            decodable=not decode_errors,
            stream_infos=stream_infos,
            decode_errors=decode_errors,
            timestamp_errors=timestamp_errors,
        )

    def _probe_audio_streams(
        self,
        uri: str,
    ) -> tuple[list[AudioStreamInfo], str | None]:

        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,codec_name,sample_rate,channels",
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

        streams = payload.get(
            "streams",
            [],
        )

        return [
            AudioStreamInfo(
                index=int(stream["index"]),
                codec_name=stream.get("codec_name"),
                sample_rate=stream.get("sample_rate"),
                channels=stream.get("channels"),
            )
            for stream in streams
            if "index" in stream
        ], None

    def _decode_audio(
        self,
        uri: str,
    ) -> tuple[list[str], list[str]]:

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            uri,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return [
                f"ffmpeg timeout after {exc.timeout}s"
            ], []

        timestamp_errors = [
            line.strip()
            for line in process.stderr.splitlines()
            if TIMESTAMP_ERROR_PATTERN.search(
                line.strip()
            )
        ]

        decode_errors = []

        if process.returncode != 0:
            decode_errors.append(
                process.stderr.strip()
            )

        return decode_errors, timestamp_errors
