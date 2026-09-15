from pathlib import Path
import shutil
import subprocess


def generate_black_rule_vod(
    output_dir: Path,
    *,
    segment_duration: float = 1.0,
) -> None:
    """Generate three short black events followed by one 62s event."""
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = output_dir / "stream.m3u8"
    master_path = output_dir / "master.m3u8"
    enables = "+".join(
        (
            "between(t,6,8)",
            "between(t,14,16)",
            "between(t,22,24)",
            "between(t,30,92)",
        )
    )
    command = [
        shutil.which("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x72:rate=10:duration=96",
        "-vf",
        (
            "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:"
            f"enable='{enables}'"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "10",
        "-keyint_min",
        "10",
        "-sc_threshold",
        "0",
        "-an",
        "-f",
        "hls",
        "-hls_time",
        str(segment_duration),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        str(output_dir / "seg_%03d.ts"),
        str(playlist_path),
    ]
    subprocess.run(command, check=True, timeout=60)

    master_path.write_text(
        (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=128x72\n'
            "stream.m3u8\n"
        ),
        encoding="ascii",
    )
