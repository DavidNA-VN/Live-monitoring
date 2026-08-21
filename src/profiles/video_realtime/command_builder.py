from collections.abc import Sequence

from media.input_resolver import ResolvedMediaInput


class VideoRealtimeCommandBuilder:
    """Builds one FFmpeg command for the assembled video filter graph."""

    def build(
        self,
        *,
        media_input: ResolvedMediaInput,
        filter_expressions: Sequence[str],
    ) -> list[str]:
        if not filter_expressions:
            raise ValueError("Video filter graph must not be empty")
        return [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-loglevel",
            "info",
            *media_input.ffmpeg_input_options,
            "-i",
            media_input.uri,
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
