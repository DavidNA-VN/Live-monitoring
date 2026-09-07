from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAST_TARGETS = (
    "tests/models/test_admission.py",
    "tests/core/test_timeline_identity.py",
    "tests/core/test_live_runtime_sliding_window.py",
    "tests/core/test_adaptive_admission_controller.py",
    "tests/core/test_segment_admission_queue.py",
    "tests/core/test_profile_scheduler_contract.py",
    "tests/core/test_runtime_health_metrics.py",
    "tests/app/test_supervisor_runtime_status.py",
    "tests/presentation/test_runtime_status_codec.py",
    "tests/checks/black_screen/test_event_reducer.py",
    "tests/checks/audio_loss/test_event_reducer.py",
    "tests/checks/video_freeze/test_event_reducer.py",
)
MEDIA_TARGETS = (
    "tests/e2e/test_mvp_api_worker_e2e.py",
    "tests/e2e/test_video_freeze_live_e2e.py",
    "tests/e2e/test_worker_media_e2e.py",
)


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "fast"
    if mode not in {"fast", "media", "all"}:
        print("Usage: run_live_edge_verification.py [fast|media|all]")
        return 2

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        (str(PROJECT_ROOT / "src"), str(PROJECT_ROOT))
    )
    targets = list(FAST_TARGETS if mode in {"fast", "all"} else ())
    if mode in {"media", "all"}:
        env["RUN_WORKER_MEDIA_E2E"] = "1"
        env["REQUIRE_MVP_E2E"] = "1"
        targets.extend(MEDIA_TARGETS)

    command = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "--basetemp=.pytest-tmp-live-edge-verification",
        "-q",
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
