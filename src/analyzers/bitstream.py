import re
import subprocess

from models.black_analysis import (
    BitstreamEvidence,
    BitstreamIssue,
    BitstreamSegmentCheck,
)
from models.segment import Segment


BITSTREAM_ERROR_PATTERN = re.compile(
    "|".join(
        [
            r"corrupt",
            r"decode.*error",
            r"error.*decode",
            r"invalid.*nal",
            r"non-existing pps",
            r"missing reference",
            r"reference picture missing",
            r"concealing .* errors",
            r"packet corrupt",
            r"dts .* invalid",
            r"pts .* invalid",
            r"timestamp.*discontinuity",
            r"continuity check failed",
        ]
    ),
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(
    r"(?:time=|pts_time:|pts_time=)"
    r"([0-9]+(?::[0-9]+){0,2}(?:\.[0-9]+)?)"
)


class BitstreamAnalyzer:

    def __init__(
        self,
        timeout: int = 20,
    ):
        self.timeout = timeout

    def analyze_segments(
        self,
        segments: list[Segment],
    ) -> BitstreamEvidence:

        checks = [
            self._check_segment(segment)
            for segment in segments
        ]

        return BitstreamEvidence(
            checked_segment_count=sum(
                1
                for check in checks
                if check.checked
            ),
            failed_segment_count=sum(
                1
                for check in checks
                if not check.checked
            ),
            has_bitstream_error=any(
                check.has_error
                for check in checks
            ),
            segment_checks=checks,
        )

    def _check_segment(
        self,
        segment: Segment,
    ) -> BitstreamSegmentCheck:

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "warning",
            "-err_detect",
            "explode",
            "-i",
            segment.uri,
            "-map",
            "0:v:0",
            "-an",
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
            return BitstreamSegmentCheck(
                sequence=segment.sequence,
                uri=segment.uri,
                checked=False,
                has_error=False,
                returncode=-1,
                analyzer_error=(
                    "ffmpeg timeout after "
                    f"{exc.timeout}s"
                ),
            )

        issues = self._extract_issues(
            process.stderr
        )

        return BitstreamSegmentCheck(
            sequence=segment.sequence,
            uri=segment.uri,
            checked=(
                process.returncode == 0
                or bool(issues)
            ),
            has_error=bool(issues),
            returncode=process.returncode,
            issues=issues,
            analyzer_error=(
                None
                if process.returncode == 0
                or issues
                else process.stderr.strip()
            ),
        )

    @staticmethod
    def _extract_issues(
        ffmpeg_output: str,
    ) -> list[BitstreamIssue]:

        issues = []

        for line in ffmpeg_output.splitlines():
            normalized = line.strip()

            if not normalized:
                continue

            if BITSTREAM_ERROR_PATTERN.search(
                normalized
            ):
                issues.append(
                    BitstreamIssue(
                        issue_type=(
                            BitstreamAnalyzer
                            ._classify_issue(
                                normalized
                            )
                        ),
                        message=normalized,
                        timestamp=(
                            BitstreamAnalyzer
                            ._parse_timestamp(
                                normalized
                            )
                        ),
                    )
                )

        return issues

    @staticmethod
    def _classify_issue(
        line: str,
    ) -> str:

        lowered = line.lower()

        if "nal" in lowered:
            return "invalid_nal"

        if "reference" in lowered:
            return "missing_reference"

        if "dts" in lowered or "pts" in lowered:
            return "timestamp"

        if "continuity" in lowered:
            return "continuity"

        if "corrupt" in lowered:
            return "corruption"

        if "decode" in lowered:
            return "decode_error"

        return "bitstream_error"

    @staticmethod
    def _parse_timestamp(
        line: str,
    ) -> float | None:

        match = TIME_PATTERN.search(line)

        if not match:
            return None

        value = match.group(1)

        if ":" not in value:
            return float(value)

        parts = [
            float(part)
            for part in value.split(":")
        ]

        seconds = 0.0

        for part in parts:
            seconds = seconds * 60 + part

        return seconds
