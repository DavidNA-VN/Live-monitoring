from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


class BenchmarkInputError(ValueError):
    pass


def canonical_fingerprint(configuration: object) -> str:
    payload = json.dumps(
        configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkInputError(f"Invalid manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkInputError("Unsupported manifest schema")
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise BenchmarkInputError("Manifest fixtures must be a non-empty list")
    names: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("name"), str):
            raise BenchmarkInputError("Each fixture requires a name")
        name = fixture["name"]
        if not name or name in names:
            raise BenchmarkInputError("Fixture names must be non-empty and unique")
        names.add(name)
        ranges = fixture.get("ranges")
        if not isinstance(ranges, list):
            raise BenchmarkInputError(f"Fixture {name} ranges must be a list")
        for item in ranges:
            _validate_range(name, item)
    return data


def _validate_range(fixture_name: str, item: object) -> None:
    if not isinstance(item, dict):
        raise BenchmarkInputError(f"Fixture {fixture_name} has invalid range")
    start = item.get("start_seconds")
    end = item.get("end_seconds")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in (start, end)
    ) or start < 0 or end <= start:
        raise BenchmarkInputError(f"Fixture {fixture_name} has invalid time range")
    if item.get("label") == "macroblocking":
        band = item.get("expected_area_ratio")
        if (
            not isinstance(band, list)
            or len(band) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in band
            )
            or not 0 <= band[0] <= band[1] <= 1
        ):
            raise BenchmarkInputError(
                f"Fixture {fixture_name} macroblocking range requires area band"
            )


def load_predictions(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkInputError(f"Invalid predictions: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkInputError("Unsupported predictions schema")
    if not isinstance(data.get("configuration"), dict):
        raise BenchmarkInputError("Predictions require configuration object")
    if not isinstance(data.get("observations"), list):
        raise BenchmarkInputError("Predictions require observations list")
    return data


def _truth_at(fixture: dict[str, Any], timestamp: float) -> tuple[bool, list[float] | None]:
    for item in fixture["ranges"]:
        if item["start_seconds"] <= timestamp < item["end_seconds"]:
            return item["label"] == "macroblocking", item.get("expected_area_ratio")
    return False, None


def evaluate(manifest: dict[str, Any], predictions: dict[str, Any]) -> dict[str, Any]:
    fixtures = {item["name"]: item for item in manifest["fixtures"]}
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    area_evaluated = 0
    area_in_band = 0
    area_absolute_error = 0.0
    rows: list[dict[str, object]] = []
    for raw in predictions["observations"]:
        if not isinstance(raw, dict):
            raise BenchmarkInputError("Observation must be an object")
        fixture_name = raw.get("fixture")
        if fixture_name not in fixtures:
            raise BenchmarkInputError(f"Unknown prediction fixture: {fixture_name}")
        timestamp = raw.get("timestamp_seconds")
        area = raw.get("affected_area_ratio")
        predicted = raw.get("candidate")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or timestamp < 0
            or isinstance(area, bool)
            or not isinstance(area, (int, float))
            or not math.isfinite(area)
            or not 0 <= area <= 1
            or not isinstance(predicted, bool)
        ):
            raise BenchmarkInputError("Observation fields are invalid")
        truth, band = _truth_at(fixtures[fixture_name], float(timestamp))
        key = "tp" if truth and predicted else "fn" if truth else "fp" if predicted else "tn"
        counts[key] += 1
        in_band: bool | None = None
        if truth and band is not None:
            area_evaluated += 1
            in_band = band[0] <= area <= band[1]
            area_in_band += int(in_band)
            if area < band[0]:
                area_absolute_error += band[0] - area
            elif area > band[1]:
                area_absolute_error += area - band[1]
        rows.append(
            {
                "fixture": fixture_name,
                "timestamp_seconds": timestamp,
                "truth": truth,
                "candidate": predicted,
                "affected_area_ratio": area,
                "area_in_expected_band": in_band,
            }
        )
    precision_denominator = counts["tp"] + counts["fp"]
    recall_denominator = counts["tp"] + counts["fn"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "configuration": predictions["configuration"],
        "configuration_fingerprint": canonical_fingerprint(
            predictions["configuration"]
        ),
        "counts": counts,
        "precision": (
            counts["tp"] / precision_denominator if precision_denominator else None
        ),
        "recall": counts["tp"] / recall_denominator if recall_denominator else None,
        "area_band_accuracy": (
            area_in_band / area_evaluated if area_evaluated else None
        ),
        "mean_area_band_distance": (
            area_absolute_error / area_evaluated if area_evaluated else None
        ),
        "observations": rows,
    }
    performance = predictions.get("performance")
    if performance is not None:
        report["performance"] = _performance_summary(performance)
    return report


def _performance_summary(performance: object) -> dict[str, float | int]:
    if not isinstance(performance, dict):
        raise BenchmarkInputError("performance must be an object")
    frame_count = performance.get("frame_count")
    wall_seconds = performance.get("wall_seconds")
    peak_bytes = performance.get("peak_bytes")
    if (
        isinstance(frame_count, bool)
        or not isinstance(frame_count, int)
        or frame_count <= 0
        or isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(wall_seconds)
        or wall_seconds <= 0
        or isinstance(peak_bytes, bool)
        or not isinstance(peak_bytes, int)
        or peak_bytes < 0
    ):
        raise BenchmarkInputError("performance fields are invalid")
    return {
        "frame_count": frame_count,
        "wall_seconds": float(wall_seconds),
        "peak_bytes": peak_bytes,
        "frames_per_second": frame_count / wall_seconds,
        "mean_frame_milliseconds": wall_seconds * 1000 / frame_count,
    }


def report_csv(report: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    fields = (
        "fixture",
        "timestamp_seconds",
        "truth",
        "candidate",
        "affected_area_ratio",
        "area_in_expected_band",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(report["observations"])
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Score macroblocking observations")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--csv-output", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.predictions is None:
        print(f"Validated {len(manifest['fixtures'])} macroblocking fixtures")
        return 0
    report = evaluate(manifest, load_predictions(args.predictions))
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="ascii", newline="\n")
    if args.csv_output is not None:
        args.csv_output.parent.mkdir(parents=True, exist_ok=True)
        args.csv_output.write_text(report_csv(report), encoding="ascii", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
