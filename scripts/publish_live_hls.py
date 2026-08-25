from __future__ import annotations

import argparse
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import m3u8


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HLS_ROOT = PROJECT_ROOT / "hls_output"


@dataclass(frozen=True)
class TemplateSegment:
    path: Path
    duration: float


@dataclass(frozen=True)
class VariantTemplate:
    playlist_uri: str
    source_dir: Path
    segments: tuple[TemplateSegment, ...]


@dataclass(frozen=True)
class PublishedSegment:
    sequence: int
    filename: str
    duration: float
    program_date_time: datetime
    discontinuity: bool
    discontinuity_sequence: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a VOD HLS fixture as a sliding live HLS stream."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=HLS_ROOT / "output",
        help="VOD HLS directory containing master.m3u8.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=HLS_ROOT / "live_black_loop",
        help="Directory exposed by scripts/serve_hls.py.",
    )
    parser.add_argument("--window-size", type=int, default=6)
    parser.add_argument(
        "--retention-segments",
        type=int,
        default=12,
        help="Keep old media files briefly for in-flight HTTP readers.",
    )
    parser.add_argument(
        "--start-sequence",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat the fixture forever with monotonically increasing URIs.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Replace an existing publisher output directory.",
    )
    parser.add_argument(
        "--max-publishes",
        type=int,
        default=None,
        help="Stop after N publishes; intended for smoke tests.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed; 1.0 publishes at media duration.",
    )
    return parser.parse_args()


def load_templates(source: Path) -> tuple[str, tuple[VariantTemplate, ...]]:
    master_path = source / "master.m3u8"
    if not master_path.is_file():
        raise FileNotFoundError(f"Master playlist not found: {master_path}")

    master_text = master_path.read_text(encoding="utf-8")
    master = m3u8.loads(master_text, uri=master_path.as_uri())
    if not master.is_variant or not master.playlists:
        raise ValueError("Source master.m3u8 has no variants")

    variants: list[VariantTemplate] = []
    expected_count: int | None = None
    expected_durations: tuple[float, ...] | None = None

    for playlist in master.playlists:
        playlist_uri = playlist.uri.replace("\\", "/")
        playlist_path = (source / Path(playlist_uri)).resolve()
        if not playlist_path.is_file():
            raise FileNotFoundError(
                f"Variant playlist not found: {playlist_path}"
            )
        media = m3u8.loads(
            playlist_path.read_text(encoding="utf-8"),
            uri=playlist_path.as_uri(),
        )
        if media.is_variant or not media.segments:
            raise ValueError(f"Invalid media playlist: {playlist_path}")

        segments: list[TemplateSegment] = []
        for segment in media.segments:
            if segment.byterange or segment.init_section or segment.key:
                raise ValueError(
                    "Live fixture publisher currently expects plain TS segments"
                )
            segment_path = (playlist_path.parent / segment.uri).resolve()
            if not segment_path.is_file():
                raise FileNotFoundError(
                    f"Template segment not found: {segment_path}"
                )
            segments.append(
                TemplateSegment(
                    path=segment_path,
                    duration=float(segment.duration),
                )
            )

        durations = tuple(item.duration for item in segments)
        if expected_count is None:
            expected_count = len(segments)
            expected_durations = durations
        elif len(segments) != expected_count or durations != expected_durations:
            raise ValueError(
                "All variants must have aligned segment count and durations"
            )

        variants.append(
            VariantTemplate(
                playlist_uri=playlist_uri,
                source_dir=playlist_path.parent,
                segments=tuple(segments),
            )
        )

    return master_text, tuple(variants)


def prepare_output(output: Path, *, reset: bool) -> None:
    resolved_root = HLS_ROOT.resolve()
    resolved_output = output.resolve()
    if resolved_output == resolved_root or resolved_root not in resolved_output.parents:
        raise ValueError(f"Output must be inside {resolved_root}")

    if resolved_output.exists():
        if not reset:
            raise FileExistsError(
                f"Output already exists: {resolved_output}; use --reset"
            )
        shutil.rmtree(resolved_output)
    resolved_output.mkdir(parents=True)


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 5:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.02 * (attempt + 1))


def render_media_playlist(window: list[PublishedSegment]) -> str:
    target_duration = max(1, int(max(item.duration for item in window) + 0.999999))
    first = window[0]
    base_discontinuity_sequence = (
        first.discontinuity_sequence - int(first.discontinuity)
    )
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{first.sequence}",
        f"#EXT-X-DISCONTINUITY-SEQUENCE:{base_discontinuity_sequence}",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for segment in window:
        if segment.discontinuity:
            lines.append("#EXT-X-DISCONTINUITY")
        program_time = segment.program_date_time.isoformat().replace(
            "+00:00", "Z"
        )
        lines.extend(
            (
                f"#EXT-X-PROGRAM-DATE-TIME:{program_time}",
                f"#EXTINF:{segment.duration:.6f},",
                segment.filename,
            )
        )
    return "\n".join(lines) + "\n"


def publish(
    *,
    source: Path,
    output: Path,
    window_size: int,
    retention_segments: int,
    start_sequence: int,
    loop: bool,
    reset: bool,
    max_publishes: int | None,
    speed: float,
) -> None:
    if window_size <= 0:
        raise ValueError("window_size must be > 0")
    if retention_segments < window_size:
        raise ValueError("retention_segments must be >= window_size")
    if start_sequence < 0:
        raise ValueError("start_sequence must be >= 0")
    if max_publishes is not None and max_publishes <= 0:
        raise ValueError("max_publishes must be > 0")
    if speed <= 0:
        raise ValueError("speed must be > 0")

    source = source.resolve()
    output = output.resolve()
    master_text, variants = load_templates(source)
    prepare_output(output, reset=reset)

    for variant in variants:
        (output / Path(variant.playlist_uri).parent).mkdir(
            parents=True, exist_ok=True
        )
    atomic_write(output / "master.m3u8", master_text)

    windows: dict[str, list[PublishedSegment]] = {
        variant.playlist_uri: [] for variant in variants
    }
    started_at = datetime.now(timezone.utc)
    program_time = started_at
    sequence = start_sequence
    template_index = 0
    publishes = 0
    discontinuity_sequence = 0

    print(f"Source fixture : {source}")
    print(f"Published live : {output}")
    print(f"Variants       : {len(variants)}")
    print(f"Window size    : {window_size}")
    print(f"Loop           : {loop}")
    print()

    while True:
        starts_new_loop = False
        if template_index >= len(variants[0].segments):
            if not loop:
                break
            template_index = 0
            discontinuity_sequence += 1
            starts_new_loop = True

        current_duration = variants[0].segments[template_index].duration
        filename = f"segment_{sequence:010d}.ts"

        for variant in variants:
            variant_output = output / Path(variant.playlist_uri).parent
            template = variant.segments[template_index]
            shutil.copy2(template.path, variant_output / filename)

            window = windows[variant.playlist_uri]
            window.append(
                PublishedSegment(
                    sequence=sequence,
                    filename=filename,
                    duration=template.duration,
                    program_date_time=program_time,
                    discontinuity=starts_new_loop,
                    discontinuity_sequence=discontinuity_sequence,
                )
            )
            del window[:-window_size]
            atomic_write(
                output / variant.playlist_uri,
                render_media_playlist(window),
            )

            expired_sequence = sequence - retention_segments
            if expired_sequence >= start_sequence:
                expired = variant_output / f"segment_{expired_sequence:010d}.ts"
                expired.unlink(missing_ok=True)

        print(
            f"Published seq={sequence} template={template_index} "
            f"duration={current_duration:.3f}s at={program_time.isoformat()}"
        )
        publishes += 1
        if max_publishes is not None and publishes >= max_publishes:
            break

        template_index += 1
        sequence += 1
        program_time += timedelta(seconds=current_duration)
        time.sleep(current_duration / speed)

    print("Publisher completed.")


def main() -> None:
    args = parse_args()
    try:
        publish(
            source=args.source,
            output=args.output,
            window_size=args.window_size,
            retention_segments=args.retention_segments,
            start_sequence=args.start_sequence,
            loop=args.loop,
            reset=args.reset,
            max_publishes=args.max_publishes,
            speed=args.speed,
        )
    except KeyboardInterrupt:
        print("\nPublisher stopped.")


if __name__ == "__main__":
    main()
