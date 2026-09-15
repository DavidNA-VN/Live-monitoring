from collections.abc import Sequence
import math
from pathlib import Path

from media.input_resolver import ResolvedMediaInput


class VideoRealtimeCommandBuilder:
    """Builds one FFmpeg command for the assembled video filter graph."""

    def build(
        self,
        *,
        media_input: ResolvedMediaInput,
        filter_expressions: Sequence[str],
        boundary_raw_output: Path | None = None,
        macroblocking_raw_output: Path | None = None,
        macroblocking_width: int = 480,
        macroblocking_height: int = 270,
        macroblocking_sampling_fps: float = 1.0,
        macroblocking_max_frames: int = 64,
    ) -> list[str]:
        if not filter_expressions and macroblocking_raw_output is None:
            raise ValueError("Video filter graph must not be empty")
        if macroblocking_raw_output is not None and (
            macroblocking_width <= 0
            or macroblocking_height <= 0
            or macroblocking_sampling_fps <= 0
            or isinstance(macroblocking_max_frames, bool)
            or not isinstance(macroblocking_max_frames, int)
            or macroblocking_max_frames <= 0
        ):
            raise ValueError("Macroblocking sample settings must be > 0")
        prefix = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-loglevel",
            "info",
            *media_input.ffmpeg_input_options,
            "-i",
            media_input.uri,
        ]
        if boundary_raw_output is not None or macroblocking_raw_output is not None:
            branch_count = 1 + int(boundary_raw_output is not None) + int(
                macroblocking_raw_output is not None
            )
            source_labels = ["analysis"]
            if boundary_raw_output is not None:
                source_labels.append("sample_source")
            if macroblocking_raw_output is not None:
                source_labels.append("macroblocking_source")
            analysis_chain = (
                ",".join(filter_expressions) if filter_expressions else "null"
            )
            graph = (
                f"[0:v:0]split={branch_count}"
                + "".join(f"[{label}]" for label in source_labels)
                + f";[analysis]{analysis_chain}[analyzed]"
            )
            if boundary_raw_output is not None:
                graph += ";[sample_source]scale=32:32,format=gray[samples]"
            if macroblocking_raw_output is not None:
                graph += (
                    ";[macroblocking_source]"
                    f"fps={macroblocking_sampling_fps:g},"
                    f"scale={macroblocking_width}:{macroblocking_height}:flags=area,"
                    "format=gray[macroblocking_samples]"
                )
            command = [
                *prefix,
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
            ]
            if boundary_raw_output is not None:
                command.extend(
                    (
                        "-map",
                        "[samples]",
                        "-an",
                        "-sn",
                        "-dn",
                        "-pix_fmt",
                        "gray",
                        "-f",
                        "rawvideo",
                        str(boundary_raw_output),
                    )
                )
            if macroblocking_raw_output is not None:
                command.extend(
                    (
                        "-map",
                        "[macroblocking_samples]",
                        "-an",
                        "-sn",
                        "-dn",
                        "-pix_fmt",
                        "gray",
                        "-frames:v",
                        str(macroblocking_max_frames),
                        "-f",
                        "rawvideo",
                        str(macroblocking_raw_output),
                    )
                )
            return command
        return [
            *prefix,
            "-map",
            "0:v:0",
            "-vf",
            ",".join(filter_expressions),
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
