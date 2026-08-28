#!/usr/bin/env python3
"""
Runner script cho các profile kiểm thử MVP E2E:
- fast: Chạy nhanh Presentation Unit Tests & Contract Tests.
- media: Chạy E2E Tests có tương tác với live HLS & FFmpeg (yêu cầu Redis & FFmpeg).
- all: Chạy toàn bộ test suites.
"""

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "fast"

    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    python_exe = str(venv_py) if venv_py.exists() else sys.executable

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)))

    if mode == "fast":
        print("=== Running MVP Fast Test Suite (Presentation & Contracts) ===")
        cmd = [python_exe, "-m", "pytest", "tests/presentation", "tests/contracts", "-v"]
    elif mode == "media":
        print("=== Running MVP Media E2E Test Suite (FastAPI + Worker + FFmpeg + Redis) ===")
        env["RUN_WORKER_MEDIA_E2E"] = "1"
        env["REQUIRE_MVP_E2E"] = "1"
        cmd = [
            python_exe,
            "-m",
            "pytest",
            "tests/e2e/test_mvp_api_worker_e2e.py",
            "-v",
        ]
    elif mode == "all":
        print("=== Running Full Regression Suite ===")
        env["RUN_WORKER_MEDIA_E2E"] = "1"
        env["REQUIRE_MVP_E2E"] = "1"
        cmd = [python_exe, "-m", "pytest", "-v"]
    else:
        print(f"Unknown mode '{mode}'. Usage: python scripts/run_mvp_e2e.py [fast|media|all]")
        return 1

    pytest_result = subprocess.run(cmd, env=env, cwd=PROJECT_ROOT)
    if pytest_result.returncode != 0:
        return pytest_result.returncode

    if mode in ("fast", "all"):
        frontend_result = subprocess.run(
            [
                "node",
                "--experimental-default-type=module",
                "--test",
                "tests/frontend/test_lifecycle_controller.mjs",
            ],
            env=env,
            cwd=PROJECT_ROOT,
        )
        return frontend_result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
