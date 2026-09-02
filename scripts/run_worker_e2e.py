from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"


def parse_args() -> tuple[str, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run monitoring worker E2E test suites (fast, media, all)."
    )
    parser.add_argument(
        "mode",
        choices=["fast", "media", "all"],
        default="fast",
        nargs="?",
        help="E2E test mode: fast (no FFmpeg), media (FFmpeg + HLS), or all",
    )
    args, extra = parser.parse_known_args()
    return args.mode, extra


def main() -> int:
    mode, extra_pytest_args = parse_args()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)

    if mode == "fast":
        marker_expr = "worker_e2e and not media_e2e"
    elif mode == "media":
        env["RUN_WORKER_MEDIA_E2E"] = "1"
        env["REQUIRE_MVP_E2E"] = "1"
        marker_expr = "media_e2e"
    elif mode == "all":
        env["RUN_WORKER_MEDIA_E2E"] = "1"
        env["REQUIRE_MVP_E2E"] = "1"
        marker_expr = "worker_e2e"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/e2e",
        "-m",
        marker_expr,
        "--basetemp=.pytest-tmp-worker-e2e",
        *extra_pytest_args,
    ]

    print(f"Running E2E tests [{mode} mode]: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env, cwd=PROJECT_ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
