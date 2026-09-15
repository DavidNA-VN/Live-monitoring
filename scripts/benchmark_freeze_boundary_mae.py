from __future__ import annotations

import argparse
import base64
import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_THRESHOLDS = (0.01, 0.02, 0.03, 0.05)
PIXEL_COUNT = 32 * 32


@dataclass(frozen=True)
class LabeledBoundaryPair:
    name: str
    expected_match: bool
    left: bytes
    right: bytes

    def __post_init__(self) -> None:
        if len(self.left) != PIXEL_COUNT or len(self.right) != PIXEL_COUNT:
            raise ValueError("each boundary sample must contain 1024 gray pixels")

    @property
    def normalized_mae(self) -> float:
        return sum(abs(a - b) for a, b in zip(self.left, self.right)) / (
            PIXEL_COUNT * 255
        )


def evaluate_pairs(pairs, thresholds=DEFAULT_THRESHOLDS):
    results = []
    for threshold in thresholds:
        counts = {"true_positive": 0, "true_negative": 0,
                  "false_positive": 0, "false_negative": 0}
        for pair in pairs:
            predicted = pair.normalized_mae <= threshold
            key = (
                "true_positive" if predicted and pair.expected_match else
                "false_positive" if predicted else
                "false_negative" if pair.expected_match else "true_negative"
            )
            counts[key] += 1
        total = sum(counts.values())
        results.append({"threshold": threshold, **counts,
                        "accuracy": (counts["true_positive"] + counts["true_negative"]) / total if total else 0.0})
    return results


def load_pairs(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("labeled pair file must contain a JSON array")
    pairs = []
    for item in data:
        expected = item["expected_match"]
        if not isinstance(expected, bool):
            raise ValueError("expected_match must be a JSON boolean")
        pairs.append(LabeledBoundaryPair(
            name=str(item["name"]), expected_match=expected,
            left=base64.b64decode(item["left_pixels_base64"], validate=True),
            right=base64.b64decode(item["right_pixels_base64"], validate=True),
        ))
    return tuple(pairs)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate gray32 normalized-MAE thresholds on labeled boundary pairs."
    )
    parser.add_argument("pairs", type=Path)
    parser.add_argument("--threshold", type=float, action="append", dest="thresholds")
    args = parser.parse_args()
    thresholds = tuple(args.thresholds) if args.thresholds else DEFAULT_THRESHOLDS
    if any(value < 0 or value > 1 for value in thresholds):
        parser.error("thresholds must be between 0 and 1")
    pairs = load_pairs(args.pairs)
    print(json.dumps({"pair_count": len(pairs), "results": evaluate_pairs(pairs, thresholds)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
