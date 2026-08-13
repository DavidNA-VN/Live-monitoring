from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass
class FrameScore:
    frame: int
    pts_time: float
    raw_score: float
    smooth_score: float = 0.0
    level: str = "NORMAL"


@dataclass
class Event:
    level: str
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    alert_frames: int
    peak_frame: int
    peak_time: float
    peak_score: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze macroblocking candidates in an HLS/m3u8 stream."
    )
    parser.add_argument("url", help="m3u8 URL. Put the URL in quotes in PowerShell.")
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Seconds to analyze. Default: 60.",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start offset in seconds for replayable/VOD HLS. Default: 0.",
    )
    parser.add_argument(
        "--period-min",
        type=int,
        default=8,
        help="Minimum block period searched by blockdetect. Default: 8.",
    )
    parser.add_argument(
        "--period-max",
        type=int,
        default=32,
        help="Maximum block period searched by blockdetect. Default: 32.",
    )
    parser.add_argument(
        "--planes",
        type=int,
        default=1,
        help="Plane mask. 1 means luma/Y only. Default: 1.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=5,
        help="Trailing moving-average window in frames. Default: 5.",
    )
    parser.add_argument(
        "--warning",
        type=float,
        default=None,
        help="Fixed warning threshold. Omit for exploratory auto-threshold.",
    )
    parser.add_argument(
        "--critical",
        type=float,
        default=None,
        help="Fixed critical threshold. Omit for exploratory auto-threshold.",
    )
    parser.add_argument(
        "--min-event-frames",
        type=int,
        default=3,
        help="Minimum suspicious frames required for an event. Default: 3.",
    )
    parser.add_argument(
        "--max-gap-frames",
        type=int,
        default=2,
        help="Normal-frame gap allowed inside one event. Default: 2.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of highest-scoring frames listed/saved. Default: 10.",
    )
    parser.add_argument(
        "--top-spacing",
        type=float,
        default=0.5,
        help="Minimum seconds between selected top frames. Default: 0.5.",
    )
    parser.add_argument(
        "--extract-top-frames",
        action="store_true",
        help="Save top candidate frames. Intended for replayable/VOD HLS.",
    )
    parser.add_argument(
        "--output-dir",
        default="blockdetect_output",
        help="Output directory. Default: blockdetect_output.",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="FFmpeg executable/path. Default: ffmpeg.",
    )
    parser.add_argument("--user-agent", default=None, help="Optional HTTP User-Agent.")
    parser.add_argument("--referer", default=None, help="Optional HTTP Referer.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar='"Name: value"',
        help="Extra HTTP header. Repeat this option when needed.",
    )
    args = parser.parse_args()

    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.start < 0:
        parser.error("--start must be >= 0")
    if not (2 <= args.period_min <= 32):
        parser.error("--period-min must be between 2 and 32")
    if not (2 <= args.period_max <= 64):
        parser.error("--period-max must be between 2 and 64")
    if args.period_min > args.period_max:
        parser.error("--period-min cannot be greater than --period-max")
    if args.window <= 0:
        parser.error("--window must be > 0")
    if args.min_event_frames <= 0:
        parser.error("--min-event-frames must be > 0")
    if args.max_gap_frames < 0:
        parser.error("--max-gap-frames must be >= 0")
    if args.top < 0:
        parser.error("--top must be >= 0")
    if args.top_spacing < 0:
        parser.error("--top-spacing must be >= 0")
    if args.warning is not None and args.critical is not None:
        if args.warning >= args.critical:
            parser.error("--warning must be lower than --critical")

    return args


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, where q is in [0, 100]."""
    if not values:
        raise ValueError("Cannot calculate percentile of an empty sequence.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * q / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]

    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def http_input_options(args: argparse.Namespace) -> list[str]:
    options: list[str] = []

    if args.user_agent:
        options.extend(["-user_agent", args.user_agent])
    if args.referer:
        options.extend(["-referer", args.referer])
    if args.header:
        header_blob = "".join(
            header.rstrip("\r\n") + "\r\n" for header in args.header
        )
        options.extend(["-headers", header_blob])

    return options


def build_analysis_command(args: argparse.Namespace) -> list[str]:
    filter_graph = (
        "setpts=PTS-STARTPTS,"
        f"blockdetect=period_min={args.period_min}:"
        f"period_max={args.period_max}:planes={args.planes},"
        "metadata=mode=print:key=lavfi.block:file=-:direct=1"
    )

    command = [
        args.ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
    ]
    command.extend(http_input_options(args))

    if args.start > 0:
        command.extend(["-ss", format_number(args.start)])

    command.extend(
        [
            "-i",
            args.url,
            "-t",
            format_number(args.duration),
            "-map",
            "0:v:0",
            "-vf",
            filter_graph,
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
    )
    return command


def parse_ffmpeg_metadata(
    command: Sequence[str],
) -> tuple[list[FrameScore], list[str], int]:
    frames: list[FrameScore] = []
    diagnostic_lines: list[str] = []

    current_frame: int | None = None
    current_pts_time: float | None = None

    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Cannot find FFmpeg executable: {command[0]!r}"
        ) from exc

    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("frame:"):
            fields = line.replace(":", ": ").split()
            try:
                frame_index = fields.index("frame:")
                pts_time_index = fields.index("pts_time:")
                current_frame = int(fields[frame_index + 1])
                current_pts_time = float(fields[pts_time_index + 1])
            except (ValueError, IndexError):
                diagnostic_lines.append(line)
            continue

        if line.startswith("lavfi.block="):
            try:
                score = float(line.split("=", 1)[1])
            except ValueError:
                diagnostic_lines.append(line)
                continue

            if current_frame is None or current_pts_time is None:
                diagnostic_lines.append(
                    f"Metadata score without frame context: {line}"
                )
                continue

            frames.append(
                FrameScore(
                    frame=current_frame,
                    pts_time=current_pts_time,
                    raw_score=score,
                )
            )
            continue

        diagnostic_lines.append(line)

    return_code = process.wait()
    return frames, diagnostic_lines, return_code


def apply_moving_average(frames: list[FrameScore], window: int) -> None:
    values: deque[float] = deque()
    running_sum = 0.0

    for item in frames:
        values.append(item.raw_score)
        running_sum += item.raw_score

        if len(values) > window:
            running_sum -= values.popleft()

        item.smooth_score = running_sum / len(values)


def choose_thresholds(
    frames: Sequence[FrameScore],
    manual_warning: float | None,
    manual_critical: float | None,
) -> tuple[float, float, str, dict[str, float]]:
    smooth_values = [item.smooth_score for item in frames]
    median_value = statistics.median(smooth_values)
    mad = statistics.median(
        [abs(value - median_value) for value in smooth_values]
    )
    robust_sigma = 1.4826 * mad

    p90 = percentile(smooth_values, 90)
    p95 = percentile(smooth_values, 95)
    p99 = percentile(smooth_values, 99)

    # These thresholds are only for ranking suspicious portions inside this
    # clip. P90/P99 deliberately guarantee candidates during the exploratory
    # run. Production thresholds must come from clean/defective reference data.
    auto_warning = p90
    auto_critical = p99

    warning = manual_warning if manual_warning is not None else auto_warning
    critical = manual_critical if manual_critical is not None else auto_critical

    if critical <= warning:
        epsilon = max(abs(warning) * 1e-6, 1e-6)
        critical = warning + epsilon

    mode = (
        "fixed"
        if manual_warning is not None and manual_critical is not None
        else "exploratory_relative"
    )

    extra = {
        "median": median_value,
        "mad": mad,
        "robust_sigma": robust_sigma,
        "p90": p90,
        "p95": p95,
        "p99": p99,
    }
    return warning, critical, mode, extra


def classify_frames(
    frames: Iterable[FrameScore],
    warning: float,
    critical: float,
) -> None:
    for item in frames:
        if item.smooth_score >= critical:
            item.level = "CRITICAL"
        elif item.smooth_score >= warning:
            item.level = "WARNING"
        else:
            item.level = "NORMAL"


def build_event(group: Sequence[FrameScore]) -> Event:
    peak = max(group, key=lambda item: item.smooth_score)
    alert_items = [item for item in group if item.level != "NORMAL"]
    level = (
        "CRITICAL"
        if any(item.level == "CRITICAL" for item in alert_items)
        else "WARNING"
    )
    first_alert = alert_items[0]
    last_alert = alert_items[-1]

    return Event(
        level=level,
        start_frame=first_alert.frame,
        end_frame=last_alert.frame,
        start_time=first_alert.pts_time,
        end_time=last_alert.pts_time,
        alert_frames=len(alert_items),
        peak_frame=peak.frame,
        peak_time=peak.pts_time,
        peak_score=peak.smooth_score,
    )


def group_events(
    frames: Sequence[FrameScore],
    min_event_frames: int,
    max_gap_frames: int,
) -> list[Event]:
    flagged_positions = [
        index for index, item in enumerate(frames) if item.level != "NORMAL"
    ]
    if not flagged_positions:
        return []

    groups: list[list[int]] = []
    current_group = [flagged_positions[0]]

    for position in flagged_positions[1:]:
        previous = current_group[-1]
        normal_gap = position - previous - 1
        if normal_gap <= max_gap_frames:
            current_group.append(position)
        else:
            groups.append(current_group)
            current_group = [position]
    groups.append(current_group)

    events: list[Event] = []
    for positions in groups:
        if len(positions) < min_event_frames:
            continue
        start = positions[0]
        end = positions[-1]
        events.append(build_event(frames[start : end + 1]))

    return events


def select_top_frames(
    frames: Sequence[FrameScore],
    count: int,
    min_spacing: float,
) -> list[FrameScore]:
    if count <= 0:
        return []

    selected: list[FrameScore] = []
    for candidate in sorted(
        frames, key=lambda item: item.smooth_score, reverse=True
    ):
        if all(
            abs(candidate.pts_time - existing.pts_time) >= min_spacing
            for existing in selected
        ):
            selected.append(candidate)
            if len(selected) >= count:
                break

    return sorted(selected, key=lambda item: item.pts_time)


def write_frames_csv(path: Path, frames: Sequence[FrameScore]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["frame", "pts_time", "raw_block", "smooth_block", "level"]
        )
        for item in frames:
            writer.writerow(
                [
                    item.frame,
                    f"{item.pts_time:.6f}",
                    f"{item.raw_score:.6f}",
                    f"{item.smooth_score:.6f}",
                    item.level,
                ]
            )


def write_events_csv(path: Path, events: Sequence[Event]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "level",
                "start_frame",
                "end_frame",
                "start_time",
                "end_time",
                "duration",
                "alert_frames",
                "peak_frame",
                "peak_time",
                "peak_score",
            ]
        )
        for event in events:
            writer.writerow(
                [
                    event.level,
                    event.start_frame,
                    event.end_frame,
                    f"{event.start_time:.6f}",
                    f"{event.end_time:.6f}",
                    f"{max(0.0, event.end_time - event.start_time):.6f}",
                    event.alert_frames,
                    event.peak_frame,
                    f"{event.peak_time:.6f}",
                    f"{event.peak_score:.6f}",
                ]
            )


def extract_candidate_frames(
    args: argparse.Namespace,
    candidates: Sequence[FrameScore],
    frames_dir: Path,
) -> list[dict[str, object]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    for rank, item in enumerate(
        sorted(candidates, key=lambda frame: frame.smooth_score, reverse=True),
        start=1,
    ):
        absolute_time = args.start + max(0.0, item.pts_time)
        output_path = frames_dir / (
            f"rank_{rank:02d}_t_{absolute_time:.3f}_"
            f"score_{item.smooth_score:.6f}.jpg"
        )

        command = [
            args.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        command.extend(http_input_options(args))
        command.extend(
            [
                "-ss",
                format_number(absolute_time),
                "-i",
                args.url,
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_path),
            ]
        )

        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        result = {
            "rank": rank,
            "relative_time": item.pts_time,
            "absolute_time": absolute_time,
            "score": item.smooth_score,
            "path": str(output_path),
            "success": completed.returncode == 0 and output_path.exists(),
        }
        if completed.returncode != 0:
            result["error"] = completed.stderr.strip()
        results.append(result)

    return results


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def print_report(
    frames: Sequence[FrameScore],
    events: Sequence[Event],
    candidates: Sequence[FrameScore],
    warning: float,
    critical: float,
    threshold_mode: str,
    stats: dict[str, float],
    output_dir: Path,
) -> None:
    raw_values = [item.raw_score for item in frames]
    smooth_values = [item.smooth_score for item in frames]

    print("\n=== BLOCKDETECT REPORT ===")
    print(f"Frames analyzed : {len(frames)}")
    print(f"Time range      : {frames[0].pts_time:.3f}s -> {frames[-1].pts_time:.3f}s")
    print(f"Raw min/mean/max: {min(raw_values):.6f} / "
          f"{statistics.fmean(raw_values):.6f} / {max(raw_values):.6f}")
    print(f"Smooth median   : {stats['median']:.6f}")
    print(f"Smooth P90/P95  : {stats['p90']:.6f} / {stats['p95']:.6f}")
    print(f"Smooth P99      : {stats['p99']:.6f}")
    print(f"MAD / robust σ  : {stats['mad']:.6f} / {stats['robust_sigma']:.6f}")
    print(f"Threshold mode  : {threshold_mode}")
    print(f"Warning         : {warning:.6f}")
    print(f"Critical        : {critical:.6f}")
    print(f"Events retained : {len(events)}")

    if threshold_mode == "exploratory_relative":
        print(
            "NOTE             : Auto thresholds only rank suspicious portions "
            "inside this clip; they are not production thresholds."
        )

    if events:
        print("\nEvents:")
        for index, event in enumerate(events, start=1):
            print(
                f"  #{index:02d} {event.level:<8} "
                f"{event.start_time:.3f}s -> {event.end_time:.3f}s "
                f"peak={event.peak_score:.6f} at {event.peak_time:.3f}s "
                f"frames={event.alert_frames}"
            )

    if candidates:
        print("\nTop candidate frames:")
        for item in sorted(
            candidates, key=lambda frame: frame.smooth_score, reverse=True
        ):
            print(
                f"  t={item.pts_time:.3f}s "
                f"frame={item.frame} "
                f"raw={item.raw_score:.6f} "
                f"smooth={item.smooth_score:.6f} "
                f"{item.level}"
            )

    print(f"\nOutputs          : {output_dir.resolve()}")


def main() -> int:
    args = parse_args()

    if shutil.which(args.ffmpeg) is None and not Path(args.ffmpeg).exists():
        print(
            f"ERROR: FFmpeg was not found: {args.ffmpeg!r}",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    command = build_analysis_command(args)
    print("Running FFmpeg blockdetect...")
    print("Command:")
    print(subprocess.list2cmdline(command))

    frames, diagnostics, return_code = parse_ffmpeg_metadata(command)

    if diagnostics:
        (output_dir / "ffmpeg_diagnostics.log").write_text(
            "\n".join(diagnostics) + "\n",
            encoding="utf-8",
        )

    if return_code != 0:
        print(
            f"ERROR: FFmpeg exited with code {return_code}. "
            f"See {output_dir / 'ffmpeg_diagnostics.log'}",
            file=sys.stderr,
        )
        return return_code

    if not frames:
        print(
            "ERROR: No lavfi.block values were parsed. "
            "Check whether this FFmpeg build includes blockdetect and whether "
            "the HLS URL/headers are valid.",
            file=sys.stderr,
        )
        return 3

    apply_moving_average(frames, args.window)
    warning, critical, threshold_mode, robust_stats = choose_thresholds(
        frames,
        args.warning,
        args.critical,
    )
    classify_frames(frames, warning, critical)

    events = group_events(
        frames,
        min_event_frames=args.min_event_frames,
        max_gap_frames=args.max_gap_frames,
    )
    top_frames = select_top_frames(
        frames,
        count=args.top,
        min_spacing=args.top_spacing,
    )

    frames_csv = output_dir / "frame_scores.csv"
    events_csv = output_dir / "events.csv"
    summary_json = output_dir / "summary.json"

    write_frames_csv(frames_csv, frames)
    write_events_csv(events_csv, events)

    extraction_results: list[dict[str, object]] = []
    if args.extract_top_frames and top_frames:
        print("Extracting candidate frames...")
        extraction_results = extract_candidate_frames(
            args,
            top_frames,
            output_dir / "candidate_frames",
        )

    raw_values = [item.raw_score for item in frames]
    smooth_values = [item.smooth_score for item in frames]
    summary = {
        "input": {
            "url": args.url,
            "start_seconds": args.start,
            "duration_seconds": args.duration,
        },
        "blockdetect": {
            "period_min": args.period_min,
            "period_max": args.period_max,
            "planes": args.planes,
            "moving_average_window_frames": args.window,
        },
        "frames_analyzed": len(frames),
        "time_range": {
            "start": frames[0].pts_time,
            "end": frames[-1].pts_time,
        },
        "raw_score": {
            "min": min(raw_values),
            "mean": statistics.fmean(raw_values),
            "max": max(raw_values),
        },
        "smooth_score": {
            "min": min(smooth_values),
            "mean": statistics.fmean(smooth_values),
            "max": max(smooth_values),
            **robust_stats,
        },
        "thresholds": {
            "mode": threshold_mode,
            "warning": warning,
            "critical": critical,
            "note": (
                "Exploratory thresholds rank suspicious intervals inside this "
                "clip. Calibrate fixed thresholds using clean and defective "
                "clips from the same content/pipeline."
                if threshold_mode == "exploratory_relative"
                else "Fixed thresholds supplied from the command line."
            ),
        },
        "events": [event.__dict__ for event in events],
        "top_frames": [
            {
                "frame": item.frame,
                "pts_time": item.pts_time,
                "raw_score": item.raw_score,
                "smooth_score": item.smooth_score,
                "level": item.level,
            }
            for item in top_frames
        ],
        "extracted_frames": extraction_results,
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print_report(
        frames=frames,
        events=events,
        candidates=top_frames,
        warning=warning,
        critical=critical,
        threshold_mode=threshold_mode,
        stats=robust_stats,
        output_dir=output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())