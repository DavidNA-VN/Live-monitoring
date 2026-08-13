import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import m3u8


HTTP_TIMEOUT = 10.0
FFMPEG_TIMEOUT = 60.0

# Một pixel được coi là đen nếu luma của nó nằm dưới ngưỡng này.
BLACK_PIXEL_THRESHOLD = 0.10

# Một frame được coi là đen nếu ít nhất 98% pixel là đen.
BLACK_PICTURE_RATIO = 0.98

# Thu thập cả các candidate ngắn để có thể nối qua biên segment.
RAW_BLACK_MIN_DURATION = 0.10

# Sau khi nối các candidate, chỉ báo lỗi nếu kéo dài ít nhất 2 giây.
BLACK_EVENT_MIN_DURATION = 2.0

# Cho phép khoảng hở nhỏ do timestamp hoặc frame cadence.
BLACK_EVENT_MERGE_GAP = 0.25


FLOAT_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"

BLACK_EVENT_PATTERN = re.compile(
    rf"black_start:(?P<start>{FLOAT_PATTERN})\s+"
    rf"black_end:(?P<end>{FLOAT_PATTERN})\s+"
    rf"black_duration:(?P<duration>{FLOAT_PATTERN})"
)


@dataclass
class RawBlackInterval:
    variant_index: int | None
    segment_index: int
    media_sequence: int
    segment_url: str

    local_start: float
    local_end: float
    local_duration: float

    timeline_start: float
    timeline_end: float


@dataclass
class BlackScreenEvent:
    code: str
    layer: str
    severity: str
    confidence: float

    variant_index: int | None

    start_time: float
    end_time: float
    duration: float

    segment_indexes: list[int]
    media_sequences: list[int]
    segment_urls: list[str]


class BlackScreenChecker:
    def __init__(
        self,
        http_timeout: float = HTTP_TIMEOUT,
        ffmpeg_timeout: float = FFMPEG_TIMEOUT,
        verbose: bool = False,
    ):
        self.ffmpeg_timeout = ffmpeg_timeout
        self.verbose = verbose

        self.client = httpx.Client(
            timeout=httpx.Timeout(http_timeout),
            follow_redirects=True,
            headers={
                "User-Agent": "media-monitor-black-checker/0.1",
                "Accept": "*/*",
            },
        )

    def close(self) -> None:
        self.client.close()

    def fetch_playlist(self, url: str) -> m3u8.M3U8:
        try:
            response = self.client.get(url)
            response.raise_for_status()

        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Quá thời gian tải playlist: {url}"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Playlist trả về HTTP "
                f"{exc.response.status_code}: {url}"
            ) from exc

        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Không thể tải playlist: {url}. Chi tiết: {exc}"
            ) from exc

        content = response.text

        if not content.lstrip().startswith("#EXTM3U"):
            raise RuntimeError(
                f"Nội dung không phải playlist M3U8 hợp lệ: {url}"
            )

        try:
            return m3u8.loads(
                content,
                uri=url,
            )

        except Exception as exc:
            raise RuntimeError(
                f"Không thể parse playlist: {url}. Chi tiết: {exc}"
            ) from exc

    def download_segment(
        self,
        segment_url: str,
        destination: Path,
    ) -> int:
        try:
            with self.client.stream("GET", segment_url) as response:
                response.raise_for_status()

                downloaded_bytes = 0

                with destination.open("wb") as output_file:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue

                        output_file.write(chunk)
                        downloaded_bytes += len(chunk)

        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Quá thời gian tải segment: {segment_url}"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Segment trả về HTTP "
                f"{exc.response.status_code}: {segment_url}"
            ) from exc

        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Không thể tải segment: {segment_url}. Chi tiết: {exc}"
            ) from exc

        if downloaded_bytes == 0:
            raise RuntimeError(
                f"Segment không có dữ liệu: {segment_url}"
            )

        return downloaded_bytes

    def run_blackdetect(
        self,
        file_path: Path,
    ) -> tuple[list[tuple[float, float, float]], list[str]]:
        """
        Chạy FFmpeg blackdetect trên một segment.

        setpts=PTS-STARTPTS đưa timestamp của mỗi segment về bắt đầu từ 0,
        giúp chuyển local timestamp sang timeline playlist dễ hơn.
        """

        black_filter = (
            "setpts=PTS-STARTPTS,"
            f"blackdetect="
            f"d={RAW_BLACK_MIN_DURATION}:"
            f"pix_th={BLACK_PIXEL_THRESHOLD}:"
            f"pic_th={BLACK_PICTURE_RATIO}"
        )

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-i",
            str(file_path),

            "-map",
            "0:v:0",

            "-vf",
            black_filter,

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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.ffmpeg_timeout,
                shell=False,
            )

        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"FFmpeg blackdetect chạy quá "
                f"{self.ffmpeg_timeout} giây."
            ) from exc

        except OSError as exc:
            raise RuntimeError(
                f"Không thể chạy FFmpeg: {exc}"
            ) from exc

        if process.returncode != 0:
            raise RuntimeError(
                "FFmpeg blackdetect không hoàn tất thành công.\n"
                + process.stderr
            )

        intervals: list[tuple[float, float, float]] = []

        for match in BLACK_EVENT_PATTERN.finditer(process.stderr):
            start = float(match.group("start"))
            end = float(match.group("end"))
            duration = float(match.group("duration"))

            if end < start:
                continue

            intervals.append(
                (
                    max(0.0, start),
                    max(0.0, end),
                    max(0.0, duration),
                )
            )

        evidence_lines = [
            line.strip()
            for line in process.stderr.splitlines()
            if "black_start:" in line
        ]

        return intervals, evidence_lines

    @staticmethod
    def get_segment_suffix(segment_url: str) -> str:
        suffix = Path(urlparse(segment_url).path).suffix
        return suffix or ".bin"

    def analyze_media_playlist(
        self,
        playlist: m3u8.M3U8,
        playlist_url: str,
        variant_index: int | None,
        temporary_directory: Path,
    ) -> dict[str, Any]:
        if playlist.is_variant:
            raise RuntimeError(
                f"Đây không phải media playlist: {playlist_url}"
            )

        media_sequence = playlist.media_sequence or 0
        timeline_offset = 0.0

        raw_intervals: list[RawBlackInterval] = []
        segment_results: list[dict[str, Any]] = []
        processing_errors: list[dict[str, Any]] = []

        print("\n" + "=" * 80)
        print(
            f"Variant       : "
            f"{'root' if variant_index is None else variant_index}"
        )
        print(f"Media playlist: {playlist_url}")
        print(f"Segments      : {len(playlist.segments)}")
        print("=" * 80)

        for segment_index, segment in enumerate(playlist.segments):
            sequence = media_sequence + segment_index
            declared_duration = float(segment.duration)
            segment_url = urljoin(playlist_url, segment.uri)

            suffix = self.get_segment_suffix(segment_url)

            local_path = (
                temporary_directory
                / f"variant_{variant_index}_"
                  f"sequence_{sequence}_"
                  f"index_{segment_index}"
                  f"{suffix}"
            )

            segment_result: dict[str, Any] = {
                "segment_index": segment_index,
                "media_sequence": sequence,
                "url": segment_url,
                "declared_duration": declared_duration,
                "timeline_start": timeline_offset,
                "timeline_end": timeline_offset + declared_duration,
                "status": "PASS",
                "downloaded_bytes": None,
                "raw_black_intervals": [],
            }

            try:
                downloaded_bytes = self.download_segment(
                    segment_url=segment_url,
                    destination=local_path,
                )

                segment_result["downloaded_bytes"] = downloaded_bytes

                detected_intervals, evidence = self.run_blackdetect(
                    file_path=local_path,
                )

                for local_start, local_end, local_duration in detected_intervals:
                    # Không cho timestamp vượt quá segment quá xa.
                    normalized_start = max(
                        0.0,
                        min(local_start, declared_duration),
                    )

                    normalized_end = max(
                        normalized_start,
                        min(
                            local_end,
                            declared_duration + BLACK_EVENT_MERGE_GAP,
                        ),
                    )

                    interval = RawBlackInterval(
                        variant_index=variant_index,
                        segment_index=segment_index,
                        media_sequence=sequence,
                        segment_url=segment_url,

                        local_start=normalized_start,
                        local_end=normalized_end,
                        local_duration=max(
                            0.0,
                            normalized_end - normalized_start,
                        ),

                        timeline_start=(
                            timeline_offset + normalized_start
                        ),
                        timeline_end=(
                            timeline_offset + normalized_end
                        ),
                    )

                    raw_intervals.append(interval)

                    segment_result[
                        "raw_black_intervals"
                    ].append(
                        asdict(interval)
                    )

                if detected_intervals:
                    segment_result["status"] = "BLACK_CANDIDATE"

                if self.verbose and evidence:
                    segment_result["evidence"] = evidence

            except RuntimeError as exc:
                segment_result["status"] = "PROCESSING_ERROR"

                processing_errors.append(
                    {
                        "segment_index": segment_index,
                        "media_sequence": sequence,
                        "url": segment_url,
                        "message": str(exc),
                    }
                )

            candidate_count = len(
                segment_result["raw_black_intervals"]
            )

            print(
                f"[{segment_index:03d}] "
                f"seq={sequence:<5} "
                f"time={timeline_offset:7.3f}s "
                f"duration={declared_duration:6.3f}s "
                f"black_candidates={candidate_count} "
                f"status={segment_result['status']}"
            )

            if self.verbose:
                print(f"      {segment_url}")

                for interval in segment_result[
                    "raw_black_intervals"
                ]:
                    print(
                        "      "
                        f"local={interval['local_start']:.3f}"
                        f"→{interval['local_end']:.3f}s "
                        f"timeline="
                        f"{interval['timeline_start']:.3f}"
                        f"→{interval['timeline_end']:.3f}s"
                    )

            segment_results.append(segment_result)
            timeline_offset += declared_duration

        black_events = self.merge_black_intervals(
            intervals=raw_intervals,
        )

        status = (
            "CONTENT_ANOMALY"
            if black_events
            else "PASS"
        )

        if processing_errors and not black_events:
            status = "INCOMPLETE"

        return {
            "variant_index": variant_index,
            "playlist_url": playlist_url,
            "media_sequence": media_sequence,
            "total_declared_duration": timeline_offset,
            "status": status,
            "segments": segment_results,
            "black_screen_events": [
                asdict(event)
                for event in black_events
            ],
            "processing_errors": processing_errors,
        }

    @staticmethod
    def merge_black_intervals(
        intervals: list[RawBlackInterval],
    ) -> list[BlackScreenEvent]:
        if not intervals:
            return []

        intervals = sorted(
            intervals,
            key=lambda item: (
                -1
                if item.variant_index is None
                else item.variant_index,
                item.timeline_start,
                item.timeline_end,
            ),
        )

        events: list[BlackScreenEvent] = []

        current_variant = intervals[0].variant_index
        current_start = intervals[0].timeline_start
        current_end = intervals[0].timeline_end

        current_segment_indexes = {
            intervals[0].segment_index
        }

        current_media_sequences = {
            intervals[0].media_sequence
        }

        current_segment_urls = {
            intervals[0].segment_url
        }

        last_media_sequence = intervals[0].media_sequence

        def finalize_current_event() -> None:
            duration = max(
                0.0,
                current_end - current_start,
            )

            if duration < BLACK_EVENT_MIN_DURATION:
                return

            severity = (
                "critical"
                if duration >= 5.0
                else "warning"
            )

            events.append(
                BlackScreenEvent(
                    code="BLACK_SCREEN",
                    layer="VIDEO_CONTENT",
                    severity=severity,
                    confidence=0.99,

                    variant_index=current_variant,

                    start_time=round(current_start, 6),
                    end_time=round(current_end, 6),
                    duration=round(duration, 6),

                    segment_indexes=sorted(
                        current_segment_indexes
                    ),
                    media_sequences=sorted(
                        current_media_sequences
                    ),
                    segment_urls=sorted(
                        current_segment_urls
                    ),
                )
            )

        for interval in intervals[1:]:
            same_variant = (
                interval.variant_index
                == current_variant
            )

            gap = (
                interval.timeline_start
                - current_end
            )

            sequence_is_contiguous = (
                interval.media_sequence
                <= last_media_sequence + 1
            )

            should_merge = (
                same_variant
                and sequence_is_contiguous
                and gap <= BLACK_EVENT_MERGE_GAP
            )

            if should_merge:
                current_end = max(
                    current_end,
                    interval.timeline_end,
                )

                current_segment_indexes.add(
                    interval.segment_index
                )

                current_media_sequences.add(
                    interval.media_sequence
                )

                current_segment_urls.add(
                    interval.segment_url
                )

                last_media_sequence = max(
                    last_media_sequence,
                    interval.media_sequence,
                )

                continue

            finalize_current_event()

            current_variant = interval.variant_index
            current_start = interval.timeline_start
            current_end = interval.timeline_end

            current_segment_indexes = {
                interval.segment_index
            }

            current_media_sequences = {
                interval.media_sequence
            }

            current_segment_urls = {
                interval.segment_url
            }

            last_media_sequence = interval.media_sequence

        finalize_current_event()

        return events

    def check(self, input_url: str) -> dict[str, Any]:
        root_playlist = self.fetch_playlist(input_url)

        report: dict[str, Any] = {
            "input_url": input_url,
            "detector": {
                "name": "BLACK_SCREEN",
                "pixel_black_threshold": BLACK_PIXEL_THRESHOLD,
                "picture_black_ratio": BLACK_PICTURE_RATIO,
                "raw_min_duration": RAW_BLACK_MIN_DURATION,
                "event_min_duration": BLACK_EVENT_MIN_DURATION,
                "merge_gap": BLACK_EVENT_MERGE_GAP,
            },
            "playlist_type": (
                "MASTER"
                if root_playlist.is_variant
                else "MEDIA"
            ),
            "status": "PASS",
            "variants": [],
        }

        with tempfile.TemporaryDirectory(
            prefix="black_screen_checker_"
        ) as temporary_path:
            temporary_directory = Path(temporary_path)

            if root_playlist.is_variant:
                for variant_index, variant in enumerate(
                    root_playlist.playlists
                ):
                    variant_url = urljoin(
                        input_url,
                        variant.uri,
                    )

                    try:
                        media_playlist = self.fetch_playlist(
                            variant_url
                        )

                        variant_result = (
                            self.analyze_media_playlist(
                                playlist=media_playlist,
                                playlist_url=variant_url,
                                variant_index=variant_index,
                                temporary_directory=temporary_directory,
                            )
                        )

                    except RuntimeError as exc:
                        variant_result = {
                            "variant_index": variant_index,
                            "playlist_url": variant_url,
                            "status": "PROCESSING_ERROR",
                            "black_screen_events": [],
                            "processing_errors": [
                                {
                                    "message": str(exc),
                                    "url": variant_url,
                                }
                            ],
                        }

                    report["variants"].append(
                        variant_result
                    )

            else:
                media_result = self.analyze_media_playlist(
                    playlist=root_playlist,
                    playlist_url=input_url,
                    variant_index=None,
                    temporary_directory=temporary_directory,
                )

                report["variants"].append(
                    media_result
                )

        all_events = [
            event
            for variant in report["variants"]
            for event in variant.get(
                "black_screen_events",
                [],
            )
        ]

        has_processing_errors = any(
            variant.get("processing_errors")
            for variant in report["variants"]
        )

        report["black_screen_events"] = all_events
        report["black_screen_event_count"] = len(all_events)

        if all_events:
            report["status"] = "CONTENT_ANOMALY"
        elif has_processing_errors:
            report["status"] = "INCOMPLETE"
        else:
            report["status"] = "PASS"

        return report


def print_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("BLACK SCREEN CHECK SUMMARY")
    print("=" * 80)

    print(f"Input       : {report['input_url']}")
    print(f"Status      : {report['status']}")
    print(
        f"Black events: "
        f"{report['black_screen_event_count']}"
    )

    events = report["black_screen_events"]

    if not events:
        print(
            "Kết luận: chưa phát hiện khoảng đen "
            "đủ thời lượng cảnh báo."
        )
        return

    for index, event in enumerate(events, start=1):
        print(f"\n[{index}] {event['code']}")
        print(f"    Layer          : {event['layer']}")
        print(f"    Severity       : {event['severity']}")
        print(f"    Confidence     : {event['confidence']}")
        print(f"    Variant        : {event['variant_index']}")
        print(
            f"    Timeline       : "
            f"{event['start_time']:.3f}s "
            f"→ {event['end_time']:.3f}s"
        )
        print(
            f"    Duration       : "
            f"{event['duration']:.3f}s"
        )
        print(
            f"    Segment indexes: "
            f"{event['segment_indexes']}"
        )
        print(
            f"    Media sequences: "
            f"{event['media_sequences']}"
        )

        for segment_url in event["segment_urls"]:
            print(f"    Segment URL    : {segment_url}")


def save_report(
    report: dict[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        json.dump(
            report,
            output_file,
            indent=2,
            ensure_ascii=False,
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phát hiện black screen trong HLS "
            "master hoặc media playlist."
        )
    )

    parser.add_argument(
        "url",
        help="URL master hoặc media playlist M3U8.",
    )

    parser.add_argument(
        "--output",
        default="reports/black_screen_report.json",
        help="Đường dẫn JSON report.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Hiện URL và interval chi tiết.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    checker = BlackScreenChecker(
        verbose=args.verbose,
    )

    try:
        report = checker.check(args.url)

    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 2

    finally:
        checker.close()

    print_summary(report)

    output_path = Path(args.output)

    save_report(
        report=report,
        output_path=output_path,
    )

    print(f"\nJSON report: {output_path.resolve()}")

    return (
        1
        if report["status"] == "CONTENT_ANOMALY"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())