import subprocess

import pytest

from core.process_runner import (
    ProcessRunner,
    ProcessStartError,
    ProcessTimeoutError,
)


def test_process_runner_success(
    monkeypatch,
):

    def fake_run(
        command,
        **kwargs,
    ):
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="hello",
            stderr="diagnostic",
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )

    runner = ProcessRunner()

    result = runner.run(
        [
            "ffmpeg",
            "-version",
        ],
        timeout=5.0,
    )

    assert result.ok
    assert result.returncode == 0
    assert result.stdout == "hello"
    assert result.stderr == "diagnostic"
    assert result.command == (
        "ffmpeg",
        "-version",
    )


def test_non_zero_returncode_is_not_exception(
    monkeypatch,
):

    def fake_run(
        command,
        **kwargs,
    ):
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="decode error",
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )

    runner = ProcessRunner()

    result = runner.run(
        [
            "ffmpeg",
            "-i",
            "bad.ts",
        ],
        timeout=5.0,
    )

    assert not result.ok
    assert result.returncode == 1
    assert result.stderr == "decode error"


def test_timeout_raises_process_timeout_error(
    monkeypatch,
):

    def fake_run(
        command,
        **kwargs,
    ):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=5.0,
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )

    runner = ProcessRunner()

    with pytest.raises(
        ProcessTimeoutError,
        match="ffmpeg timed out after 5.000s",
    ):
        runner.run(
            [
                "ffmpeg",
                "-version",
            ],
            timeout=5.0,
        )


def test_start_failure_raises_process_start_error(
    monkeypatch,
):

    def fake_run(
        command,
        **kwargs,
    ):
        raise FileNotFoundError(
            "missing executable"
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )

    runner = ProcessRunner()

    with pytest.raises(
        ProcessStartError,
        match="Unable to execute ffmpeg",
    ):
        runner.run(
            [
                "ffmpeg",
                "-version",
            ],
            timeout=5.0,
        )


def test_input_text_is_forwarded_to_subprocess(
    monkeypatch,
):
    captured = {}

    def fake_run(
        command,
        **kwargs,
    ):
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )

    runner = ProcessRunner()

    runner.run(
        [
            "ffmpeg",
            "-f",
            "concat",
        ],
        timeout=5.0,
        input_text="ffconcat version 1.0\n",
    )

    assert captured["input"] == (
        "ffconcat version 1.0\n"
    )
