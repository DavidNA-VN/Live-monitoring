from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_monitoring_test_cases import CASES, DEFAULT_OUTPUT
from scripts.publish_live_hls import HLS_ROOT, publish


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish one monitoring fixture as a sliding live HLS stream."
    )
    parser.add_argument(
        "case",
        choices=tuple(item.name for item in CASES),
        help="Deterministic monitoring case to publish.",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-root", type=Path, default=HLS_ROOT / "live_cases")
    parser.add_argument("--window-size", type=int, default=6)
    parser.add_argument("--retention-segments", type=int, default=12)
    parser.add_argument("--start-sequence", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--max-publishes", type=int)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    source = args.source_root / args.case
    if not (source / "master.m3u8").is_file():
        raise FileNotFoundError(
            f"Case is not generated: {source}; run "
            "python scripts/generate_monitoring_test_cases.py first"
        )
    output = args.output_root / args.case
    publish(
        source=source,
        output=output,
        window_size=args.window_size,
        retention_segments=args.retention_segments,
        start_sequence=args.start_sequence,
        loop=True,
        reset=args.reset,
        max_publishes=args.max_publishes,
        speed=args.speed,
    )
    print(f"Master URL: http://127.0.0.1:8000/live_cases/{args.case}/master.m3u8")


if __name__ == "__main__":
    main()
