from collections.abc import Sequence

from media.input_resolver import ResolvedMediaInput


class AudioRealtimeCommandBuilder:
    """Builds one FFmpeg command for the assembled audio filter graph."""

    def __init__(self, *, track_index: int = 0) -> None:
        if track_index < 0:
            raise ValueError("track_index must be >= 0")
        self.track_index = track_index

    def build(
        self,
        *,
        media_input: ResolvedMediaInput,
        filter_expressions: Sequence[str],
        track_index: int | None = None,
    ) -> list[str]:
        if not filter_expressions:
            raise ValueError("Audio filter graph must not be empty")
        selected_track = self.track_index if track_index is None else track_index
        if selected_track < 0:
            raise ValueError("track_index must be >= 0")
        return [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-loglevel",
            "info",
            "-xerror",
            *media_input.ffmpeg_input_options,
            "-i",
            media_input.uri,
            "-map",
            f"0:a:{selected_track}",
            "-af",
            ",".join(filter_expressions),
            "-vn",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
