from __future__ import annotations

import shutil
import subprocess
import sys

import httpx
import m3u8
import redis


def check_command(command: str) -> None:
    """Check whether an external executable exists and can run."""
    executable = shutil.which(command)

    if executable is None:
        raise RuntimeError(f"Không tìm thấy {command} trong PATH")

    result = subprocess.run(
        [command, "-version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{command} chạy thất bại:\n{result.stderr.strip()}"
        )

    first_line = result.stdout.splitlines()[0]
    print(f"{command}: OK")
    print(f"  {first_line}")


def check_redis() -> None:
    """Connect to the local Redis container."""
    client = redis.Redis(
        host="127.0.0.1",
        port=6379,
        decode_responses=True,
        socket_connect_timeout=3,
    )

    if client.ping() is not True:
        raise RuntimeError("Redis không trả về PONG")

    client.set("media-monitor:environment", "ready", ex=60)
    value = client.get("media-monitor:environment")

    print("Redis: OK")
    print(f"  Test value: {value}")


def main() -> None:
    print(f"Python: {sys.version.split()[0]}")
    print(f"httpx: {httpx.__version__}")
    print(f"m3u8 module: {m3u8.__name__}")
    print(f"redis-py: {redis.__version__}")
    print()

    check_command("ffmpeg")
    check_command("ffprobe")
    check_redis()

    print("\nCHẶNG 0: ENVIRONMENT READY")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nENVIRONMENT CHECK FAILED: {exc}")
        raise SystemExit(1)