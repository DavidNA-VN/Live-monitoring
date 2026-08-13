import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import m3u8


DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_TOOL_TIMEOUT = 60.0
MAX_EVIDENCE_LINES = 30


@dataclass
class CheckError:
    code: str
    message: str
    url: str

    variant_index: int | None = None
    segment_index: int | None = None
    media_sequence: int | None = None
    http_status: int | None = None

    evidence: list[str] | None = None


class HLSChecker:
    def __init__(
        self,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        tool_timeout: float = DEFAULT_TOOL_TIMEOUT,
        require_audio: bool = False,
    ):
        self.http_timeout = http_timeout
        self.tool_timeout = tool_timeout
        self.require_audio = require_audio

        self.errors: list[CheckError] = []
        self.temp_dir: Path | None = None

        self.client = httpx.Client(
            timeout=httpx.Timeout(http_timeout),
            follow_redirects=True,
            headers={
                "User-Agent": "media-monitor/0.2",
                "Accept": "*/*",
            },
        )

    def close(self) -> None:
        self.client.close()

    def add_error(
        self,
        code: str,
        message: str,
        url: str,
        variant_index: int | None = None,
        segment_index: int | None = None,
        media_sequence: int | None = None,
        http_status: int | None = None,
        evidence: list[str] | None = None,
    ) -> None:
        self.errors.append(
            CheckError(
                code=code,
                message=message,
                url=url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
                http_status=http_status,
                evidence=evidence,
            )
        )

    @staticmethod
    def normalize_stderr(stderr: str) -> list[str]:
        """
        Chuẩn hóa stderr thành danh sách bằng chứng ngắn gọn.
        """

        lines = []

        for line in stderr.splitlines():
            line = line.strip()

            if line and line not in lines:
                lines.append(line)

        return lines[:MAX_EVIDENCE_LINES]

    def download_text(
        self,
        url: str,
        variant_index: int | None = None,
    ) -> str | None:
        try:
            response = self.client.get(url)

        except httpx.TimeoutException:
            self.add_error(
                code="PLAYLIST_TIMEOUT",
                message="Quá thời gian tải playlist.",
                url=url,
                variant_index=variant_index,
            )
            return None

        except httpx.RequestError as exc:
            self.add_error(
                code="PLAYLIST_REQUEST_ERROR",
                message=f"Không thể request playlist: {exc}",
                url=url,
                variant_index=variant_index,
            )
            return None

        if response.status_code != 200:
            self.add_error(
                code="PLAYLIST_HTTP_ERROR",
                message=(
                    f"Playlist trả về HTTP {response.status_code} "
                    f"{response.reason_phrase}"
                ),
                url=url,
                variant_index=variant_index,
                http_status=response.status_code,
            )
            return None

        text = response.text

        if not text.strip():
            self.add_error(
                code="PLAYLIST_EMPTY",
                message="Playlist không có nội dung.",
                url=url,
                variant_index=variant_index,
            )
            return None

        if not text.lstrip().startswith("#EXTM3U"):
            self.add_error(
                code="PLAYLIST_INVALID_HEADER",
                message="Playlist không bắt đầu bằng #EXTM3U.",
                url=url,
                variant_index=variant_index,
            )
            return None

        return text

    def parse_playlist(
        self,
        playlist_text: str,
        playlist_url: str,
        variant_index: int | None = None,
    ) -> m3u8.M3U8 | None:
        try:
            return m3u8.loads(
                playlist_text,
                uri=playlist_url,
            )

        except Exception as exc:
            self.add_error(
                code="PLAYLIST_PARSE_ERROR",
                message=f"Không thể parse playlist: {exc}",
                url=playlist_url,
                variant_index=variant_index,
            )
            return None

    def create_segment_temp_path(
        self,
        segment_url: str,
        variant_index: int | None,
        segment_index: int,
        media_sequence: int,
    ) -> Path:
        if self.temp_dir is None:
            raise RuntimeError("Temporary directory chưa được khởi tạo.")

        url_path = urlparse(segment_url).path
        suffix = Path(url_path).suffix or ".bin"

        variant_name = (
            "root"
            if variant_index is None
            else f"variant_{variant_index}"
        )

        filename = (
            f"{variant_name}_"
            f"sequence_{media_sequence}_"
            f"index_{segment_index}"
            f"{suffix}"
        )

        return self.temp_dir / filename

    def download_segment(
        self,
        segment_url: str,
        destination: Path,
        variant_index: int | None,
        segment_index: int,
        media_sequence: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "url": segment_url,
            "status": "PASS",
            "http_status": None,
            "content_length_header": None,
            "downloaded_bytes": 0,
            "local_path": str(destination),
        }

        try:
            with self.client.stream("GET", segment_url) as response:
                result["http_status"] = response.status_code

                if response.status_code != 200:
                    result["status"] = "ERROR"

                    error_code = (
                        "SEGMENT_NOT_FOUND"
                        if response.status_code == 404
                        else "SEGMENT_HTTP_ERROR"
                    )

                    self.add_error(
                        code=error_code,
                        message=(
                            f"Segment trả về HTTP {response.status_code} "
                            f"{response.reason_phrase}"
                        ),
                        url=segment_url,
                        variant_index=variant_index,
                        segment_index=segment_index,
                        media_sequence=media_sequence,
                        http_status=response.status_code,
                    )

                    return result

                content_length = response.headers.get("content-length")

                if content_length is not None:
                    try:
                        result["content_length_header"] = int(content_length)
                    except ValueError:
                        result["content_length_header"] = None

                downloaded_bytes = 0

                with destination.open("wb") as output_file:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue

                        output_file.write(chunk)
                        downloaded_bytes += len(chunk)

                result["downloaded_bytes"] = downloaded_bytes

                if downloaded_bytes == 0:
                    result["status"] = "ERROR"

                    self.add_error(
                        code="SEGMENT_EMPTY",
                        message="Segment tồn tại nhưng không có dữ liệu.",
                        url=segment_url,
                        variant_index=variant_index,
                        segment_index=segment_index,
                        media_sequence=media_sequence,
                    )

                    return result

                declared_size = result["content_length_header"]

                if (
                    declared_size is not None
                    and downloaded_bytes != declared_size
                ):
                    result["status"] = "ERROR"

                    self.add_error(
                        code="SEGMENT_SIZE_MISMATCH",
                        message=(
                            "Kích thước tải về không khớp Content-Length: "
                            f"declared={declared_size}, "
                            f"downloaded={downloaded_bytes}."
                        ),
                        url=segment_url,
                        variant_index=variant_index,
                        segment_index=segment_index,
                        media_sequence=media_sequence,
                    )

        except httpx.TimeoutException:
            result["status"] = "ERROR"

            self.add_error(
                code="SEGMENT_TIMEOUT",
                message="Quá thời gian tải segment.",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

        except httpx.RequestError as exc:
            result["status"] = "ERROR"

            self.add_error(
                code="SEGMENT_REQUEST_ERROR",
                message=f"Không thể request segment: {exc}",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

        except OSError as exc:
            result["status"] = "ERROR"

            self.add_error(
                code="SEGMENT_WRITE_ERROR",
                message=f"Không thể lưu segment tạm: {exc}",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

        return result

    def run_ffprobe(
        self,
        file_path: Path,
        segment_url: str,
        variant_index: int | None,
        segment_index: int,
        media_sequence: int,
    ) -> dict[str, Any]:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(file_path),
        ]

        result_data: dict[str, Any] = {
            "status": "PASS",
            "return_code": None,
            "format_name": None,
            "duration": None,
            "streams": [],
            "stderr": [],
        }

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.tool_timeout,
                shell=False,
            )

        except subprocess.TimeoutExpired:
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFPROBE_TIMEOUT",
                message="ffprobe chạy quá thời gian cho phép.",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

            return result_data

        except OSError as exc:
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFPROBE_EXECUTION_ERROR",
                message=f"Không thể chạy ffprobe: {exc}",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

            return result_data

        result_data["return_code"] = process.returncode

        stderr_lines = self.normalize_stderr(process.stderr)
        result_data["stderr"] = stderr_lines

        try:
            probe_json = json.loads(process.stdout) if process.stdout else {}

        except json.JSONDecodeError as exc:
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFPROBE_OUTPUT_INVALID",
                message=f"ffprobe trả về JSON không hợp lệ: {exc}",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
                evidence=stderr_lines,
            )

            return result_data

        format_info = probe_json.get("format", {})
        streams = probe_json.get("streams", [])

        result_data["format_name"] = format_info.get("format_name")
        result_data["duration"] = format_info.get("duration")

        result_data["streams"] = [
            {
                "index": stream.get("index"),
                "codec_type": stream.get("codec_type"),
                "codec_name": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "sample_rate": stream.get("sample_rate"),
                "channels": stream.get("channels"),
                "time_base": stream.get("time_base"),
                "start_time": stream.get("start_time"),
                "duration": stream.get("duration"),
            }
            for stream in streams
        ]

        if process.returncode != 0:
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFPROBE_ERROR",
                message=(
                    f"ffprobe kết thúc với mã lỗi "
                    f"{process.returncode}."
                ),
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
                evidence=stderr_lines,
            )

        elif stderr_lines:
            # ffprobe có thể phát hiện lỗi nhưng vẫn trả return code bằng 0.
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFPROBE_DETECTED_ERROR",
                message=(
                    "ffprobe phát hiện lỗi trong container hoặc bitstream "
                    "dù vẫn đọc được segment."
                ),
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
                evidence=stderr_lines,
            )

        video_streams = [
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
        ]

        audio_streams = [
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
        ]

        if not video_streams:
            result_data["status"] = "ERROR"

            self.add_error(
                code="VIDEO_STREAM_MISSING",
                message="Không tìm thấy video stream trong segment.",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

        if self.require_audio and not audio_streams:
            result_data["status"] = "ERROR"

            self.add_error(
                code="AUDIO_STREAM_MISSING",
                message="Không tìm thấy audio stream trong segment.",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

        return result_data

    def run_ffmpeg_decode(
        self,
        file_path: Path,
        segment_url: str,
        variant_index: int | None,
        segment_index: int,
        media_sequence: int,
    ) -> dict[str, Any]:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",

            "-err_detect",
            "crccheck+bitstream+buffer+careful",

            "-i",
            str(file_path),

            # Chỉ giải mã video và audio.
            # Dấu ? giúp command không thất bại nếu thiếu audio.
            "-map",
            "0:v?",
            "-map",
            "0:a?",

            "-sn",
            "-dn",

            "-f",
            "null",
            "-",
        ]

        result_data: dict[str, Any] = {
            "status": "PASS",
            "return_code": None,
            "stderr": [],
        }

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.tool_timeout,
                shell=False,
            )

        except subprocess.TimeoutExpired:
            result_data["status"] = "ERROR"

            self.add_error(
                code="DECODE_TIMEOUT",
                message="FFmpeg decode chạy quá thời gian cho phép.",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

            return result_data

        except OSError as exc:
            result_data["status"] = "ERROR"

            self.add_error(
                code="FFMPEG_EXECUTION_ERROR",
                message=f"Không thể chạy FFmpeg: {exc}",
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
            )

            return result_data

        result_data["return_code"] = process.returncode

        stderr_lines = self.normalize_stderr(process.stderr)
        result_data["stderr"] = stderr_lines

        # Với -v error, nếu stderr có nội dung thì FFmpeg đã ghi nhận
        # ít nhất một lỗi ở mức error.
        if process.returncode != 0 or stderr_lines:
            result_data["status"] = "ERROR"

            self.add_error(
                code="DECODE_ERROR",
                message=(
                    "FFmpeg phát hiện lỗi khi giải mã segment. "
                    f"Return code: {process.returncode}."
                ),
                url=segment_url,
                variant_index=variant_index,
                segment_index=segment_index,
                media_sequence=media_sequence,
                evidence=stderr_lines,
            )

        return result_data

    def analyze_segment(
        self,
        segment_url: str,
        variant_index: int | None,
        segment_index: int,
        media_sequence: int,
        declared_duration: float | None,
    ) -> dict[str, Any]:
        errors_before = len(self.errors)

        local_path = self.create_segment_temp_path(
            segment_url=segment_url,
            variant_index=variant_index,
            segment_index=segment_index,
            media_sequence=media_sequence,
        )

        result: dict[str, Any] = {
            "segment_index": segment_index,
            "media_sequence": media_sequence,
            "declared_duration": declared_duration,
            "url": segment_url,
            "status": "PASS",
            "download": None,
            "ffprobe": None,
            "decode": None,
        }

        download_result = self.download_segment(
            segment_url=segment_url,
            destination=local_path,
            variant_index=variant_index,
            segment_index=segment_index,
            media_sequence=media_sequence,
        )

        result["download"] = download_result

        if download_result["status"] == "ERROR":
            result["status"] = "ERROR"
            return result

        probe_result = self.run_ffprobe(
            file_path=local_path,
            segment_url=segment_url,
            variant_index=variant_index,
            segment_index=segment_index,
            media_sequence=media_sequence,
        )

        result["ffprobe"] = probe_result

        # Vẫn chạy FFmpeg kể cả khi ffprobe báo lỗi.
        # Có trường hợp ffprobe đọc được ít thông tin nhưng decoder
        # cung cấp thêm bằng chứng cụ thể hơn.
        decode_result = self.run_ffmpeg_decode(
            file_path=local_path,
            segment_url=segment_url,
            variant_index=variant_index,
            segment_index=segment_index,
            media_sequence=media_sequence,
        )

        result["decode"] = decode_result

        if len(self.errors) > errors_before:
            result["status"] = "ERROR"

        return result

    def check_media_playlist(
        self,
        playlist: m3u8.M3U8,
        playlist_url: str,
        variant_index: int | None = None,
        variant_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        media_sequence = playlist.media_sequence or 0
        playlist_mode = "VOD" if playlist.is_endlist else "LIVE"

        result: dict[str, Any] = {
            "playlist_type": "MEDIA",
            "playlist_mode": playlist_mode,
            "playlist_url": playlist_url,
            "target_duration": playlist.target_duration,
            "media_sequence": media_sequence,
            "segment_count": len(playlist.segments),
            "variant_info": variant_info,
            "segments": [],
            "status": "PASS",
        }

        print(f"\nMedia playlist : {playlist_url}")
        print(f"Mode           : {playlist_mode}")
        print(f"Target duration: {playlist.target_duration}")
        print(f"Media sequence : {media_sequence}")
        print(f"Segments       : {len(playlist.segments)}")
        print("-" * 80)

        if not playlist.segments:
            result["status"] = "ERROR"

            self.add_error(
                code="PLAYLIST_NO_SEGMENTS",
                message="Media playlist không chứa segment.",
                url=playlist_url,
                variant_index=variant_index,
            )

            return result

        for index, segment in enumerate(playlist.segments):
            sequence = media_sequence + index
            segment_url = urljoin(playlist_url, segment.uri)

            print(
                f"[{index:03d}] "
                f"sequence={sequence} "
                f"duration={segment.duration:.3f}s"
            )
            print(f"      {segment_url}")

            segment_result = self.analyze_segment(
                segment_url=segment_url,
                variant_index=variant_index,
                segment_index=index,
                media_sequence=sequence,
                declared_duration=segment.duration,
            )

            result["segments"].append(segment_result)

            download = segment_result["download"]
            probe = segment_result["ffprobe"]
            decode = segment_result["decode"]

            print(
                f"      Download : "
                f"{download['status']} "
                f"(HTTP {download['http_status']})"
            )

            if probe is not None:
                print(f"      ffprobe  : {probe['status']}")

            if decode is not None:
                print(f"      Decode   : {decode['status']}")

            print(f"      Result   : {segment_result['status']}")

            if segment_result["status"] == "ERROR":
                result["status"] = "ERROR"

        return result

    def check_master_playlist(
        self,
        master_playlist: m3u8.M3U8,
        master_url: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "playlist_type": "MASTER",
            "playlist_url": master_url,
            "variant_count": len(master_playlist.playlists),
            "variants": [],
            "status": "PASS",
        }

        print("\nPlaylist type: MASTER")
        print(f"Master URL   : {master_url}")
        print(f"Variants     : {len(master_playlist.playlists)}")

        if not master_playlist.playlists:
            result["status"] = "ERROR"

            self.add_error(
                code="MASTER_NO_VARIANTS",
                message="Master playlist không chứa variant.",
                url=master_url,
            )

            return result

        for index, variant in enumerate(master_playlist.playlists):
            stream_info = variant.stream_info

            resolution = None

            if stream_info.resolution:
                resolution = (
                    f"{stream_info.resolution[0]}"
                    f"x{stream_info.resolution[1]}"
                )

            variant_info = {
                "variant_index": index,
                "bandwidth": stream_info.bandwidth,
                "average_bandwidth": stream_info.average_bandwidth,
                "resolution": resolution,
                "codecs": stream_info.codecs,
            }

            variant_url = urljoin(master_url, variant.uri)

            print("\n" + "=" * 80)
            print(f"Variant #{index}")
            print(f"URL        : {variant_url}")
            print(f"Resolution : {resolution}")
            print(f"Bandwidth  : {stream_info.bandwidth}")
            print(f"Codecs     : {stream_info.codecs}")

            variant_text = self.download_text(
                variant_url,
                variant_index=index,
            )

            if variant_text is None:
                result["status"] = "ERROR"

                result["variants"].append(
                    {
                        "variant_info": variant_info,
                        "playlist_url": variant_url,
                        "status": "ERROR",
                        "segments": [],
                    }
                )
                continue

            variant_playlist = self.parse_playlist(
                playlist_text=variant_text,
                playlist_url=variant_url,
                variant_index=index,
            )

            if variant_playlist is None:
                result["status"] = "ERROR"
                continue

            if variant_playlist.is_variant:
                self.add_error(
                    code="NESTED_MASTER_UNSUPPORTED",
                    message=(
                        "Variant trỏ tới một master playlist khác. "
                        "Phiên bản hiện tại chưa xử lý nested master."
                    ),
                    url=variant_url,
                    variant_index=index,
                )

                result["status"] = "ERROR"
                continue

            variant_result = self.check_media_playlist(
                playlist=variant_playlist,
                playlist_url=variant_url,
                variant_index=index,
                variant_info=variant_info,
            )

            result["variants"].append(variant_result)

            if variant_result["status"] == "ERROR":
                result["status"] = "ERROR"

        return result

    def check(self, input_url: str) -> dict[str, Any]:
        self.errors.clear()

        report: dict[str, Any] = {
            "input_url": input_url,
            "status": "PASS",
            "playlist": None,
            "errors": [],
        }

        # Segment chỉ tồn tại tạm thời trong lúc kiểm tra.
        with tempfile.TemporaryDirectory(
            prefix="media_monitor_segments_"
        ) as temporary_directory:
            self.temp_dir = Path(temporary_directory)

            playlist_text = self.download_text(input_url)

            if playlist_text is None:
                report["status"] = "ERROR"
                report["errors"] = [
                    asdict(error)
                    for error in self.errors
                ]
                return report

            playlist = self.parse_playlist(
                playlist_text=playlist_text,
                playlist_url=input_url,
            )

            if playlist is None:
                report["status"] = "ERROR"
                report["errors"] = [
                    asdict(error)
                    for error in self.errors
                ]
                return report

            if playlist.is_variant:
                playlist_result = self.check_master_playlist(
                    master_playlist=playlist,
                    master_url=input_url,
                )
            else:
                print("\nPlaylist type: MEDIA")

                playlist_result = self.check_media_playlist(
                    playlist=playlist,
                    playlist_url=input_url,
                )

            report["playlist"] = playlist_result

        self.temp_dir = None

        report["errors"] = [
            asdict(error)
            for error in self.errors
        ]

        if self.errors:
            report["status"] = "ERROR"

        return report


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


def print_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("CHECK SUMMARY")
    print("=" * 80)

    print(f"Input : {report['input_url']}")
    print(f"Status: {report['status']}")

    errors = report["errors"]

    if not errors:
        print("Errors: 0")
        print("Kết luận: chưa phát hiện lỗi HTTP, container hoặc decode.")
        return

    print(f"Errors: {len(errors)}")

    for index, error in enumerate(errors, start=1):
        print(f"\n[{index}] {error['code']}")
        print(f"    Message       : {error['message']}")
        print(f"    URL           : {error['url']}")

        if error["variant_index"] is not None:
            print(f"    Variant       : {error['variant_index']}")

        if error["segment_index"] is not None:
            print(f"    Segment index : {error['segment_index']}")

        if error["media_sequence"] is not None:
            print(f"    Media sequence: {error['media_sequence']}")

        if error["http_status"] is not None:
            print(f"    HTTP status   : {error['http_status']}")

        if error["evidence"]:
            print("    Evidence:")

            for evidence_line in error["evidence"]:
                print(f"      - {evidence_line}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Kiểm tra HLS playlist, HTTP segment, "
            "container và khả năng giải mã."
        )
    )

    parser.add_argument(
        "url",
        help="URL của master hoặc media playlist .m3u8",
    )

    parser.add_argument(
        "--output",
        default="reports/hls_report.json",
        help="Đường dẫn file JSON report.",
    )

    parser.add_argument(
        "--http-timeout",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT,
        help="HTTP timeout tính bằng giây.",
    )

    parser.add_argument(
        "--tool-timeout",
        type=float,
        default=DEFAULT_TOOL_TIMEOUT,
        help="Timeout cho ffprobe/FFmpeg trên mỗi segment.",
    )

    parser.add_argument(
        "--require-audio",
        action="store_true",
        help="Báo lỗi nếu segment không có audio stream.",
    )

    return parser.parse_args()


def check_required_tools() -> bool:
    missing_tools = []

    for tool in ("ffprobe", "ffmpeg"):
        if shutil.which(tool) is None:
            missing_tools.append(tool)

    if missing_tools:
        print(
            "Lỗi: không tìm thấy công cụ trong PATH: "
            + ", ".join(missing_tools)
        )
        return False

    return True


def main() -> int:
    if not check_required_tools():
        return 2

    args = parse_arguments()

    checker = HLSChecker(
        http_timeout=args.http_timeout,
        tool_timeout=args.tool_timeout,
        require_audio=args.require_audio,
    )

    try:
        report = checker.check(args.url)
    finally:
        checker.close()

    print_summary(report)

    output_path = Path(args.output)
    save_report(report, output_path)

    print(f"\nJSON report: {output_path.resolve()}")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())