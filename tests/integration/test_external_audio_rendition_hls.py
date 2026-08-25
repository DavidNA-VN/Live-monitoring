from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest

from core.context import build_monitoring_context
from models.analysis import AnalysisRequirement
from models.audio import AudioTrackHint, AudioTrackPresence
from models.rendition import MediaRenditionKind
from profiles.audio_realtime import AudioRealtimeProfile


pytestmark = pytest.mark.integration


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return


def _generate(ffmpeg: str, command: list[str], cwd: Path) -> None:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *command],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"FFmpeg fixture generation failed: {completed.stderr}")


def test_external_audio_group_is_polled_and_decoded_once(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")
    for directory in ("audio", "low", "high"):
        (tmp_path / directory).mkdir()

    _generate(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=2",
            "-c:a",
            "aac",
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_list_size",
            "0",
            "-hls_segment_filename",
            "audio/segment_%03d.ts",
            "audio/playlist.m3u8",
        ],
        tmp_path,
    )
    for name, size in (("low", "64x64"), ("high", "96x64")):
        _generate(
            ffmpeg,
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=blue:s={size}:r=10:d=2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-an",
                "-f",
                "hls",
                "-hls_time",
                "2",
                "-hls_list_size",
                "0",
                "-hls_segment_filename",
                f"{name}/segment_%03d.ts",
                f"{name}/playlist.m3u8",
            ],
            tmp_path,
        )

    (tmp_path / "master.m3u8").write_text(
        """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="main",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,STABLE-RENDITION-ID="audio-en",URI="audio/playlist.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=64x64,CODECS="avc1.42c00a,mp4a.40.2",AUDIO="main"
low/playlist.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=96x64,CODECS="avc1.42c00a,mp4a.40.2",AUDIO="main"
high/playlist.m3u8
""",
        encoding="ascii",
    )
    handler = partial(QuietHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = AudioRealtimeProfile(timeout=10.0, track_index=2)
    try:
        port = server.server_address[1]
        context = build_monitoring_context(
            f"http://127.0.0.1:{port}/master.m3u8"
        )
        audio_renditions = [
            item
            for item in context.variants
            if item.rendition_kind is MediaRenditionKind.AUDIO
        ]
        assert len(audio_renditions) == 1
        assert all(
            item.audio_track_hint is AudioTrackHint.EXTERNAL
            for item in context.variants
            if item.rendition_kind is MediaRenditionKind.VARIANT
        )
        rendition = audio_renditions[0]
        snapshot = context.snapshot_for_variant(rendition)
        assert snapshot is not None
        analysis = profile.analyze(
            snapshot.segments[0]
        ).require_audio_realtime()

        assert analysis.checked is True, analysis.error
        assert analysis.presence is AudioTrackPresence.PRESENT
        assert analysis.require_output(
            AnalysisRequirement.SILENCE_INTERVALS,
            tuple,
        )
    finally:
        profile.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
