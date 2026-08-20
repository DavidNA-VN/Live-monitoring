from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ProcessResult:  # chuẩn hoá kết quả của 1 process, bao gồm command, returncode, stdout, stderr
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ProcessExecutionError(RuntimeError):
    """
    Base error for process execution infrastructure.

    Non-zero returncode is not an infrastructure error.
    A process that starts and exits still returns ProcessResult.
    """

    def __init__(
        self,
        message: str,
        command: Sequence[str],
    ):
        super().__init__(message)
        self.command = tuple(command)


class ProcessTimeoutError(ProcessExecutionError):

    def __init__(
        self,
        command: Sequence[str],
        timeout: float,
    ):
        executable = (
            command[0]
            if command
            else "<unknown>"
        )

        super().__init__(
            (
                f"{executable} timed out "
                f"after {timeout:.3f}s"
            ),
            command,
        )

        self.timeout = timeout


class ProcessStartError(ProcessExecutionError):

    def __init__(
        self,
        command: Sequence[str],
        error: OSError,
    ):
        executable = (
            command[0]
            if command
            else "<unknown>"
        )

        super().__init__(
            f"Unable to execute {executable}: {error}",
            command,
        )

        self.error = error


class ProcessRunner:  #class chạy command

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        input_text: str | None = None,
    ) -> ProcessResult:

        command_tuple = tuple(command)

        try:
            completed = subprocess.run(
                list(command_tuple),
                input=input_text,
                stdout=subprocess.PIPE,  # không in thẳng ra terminal, mà lưu vào completed.stdout
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessTimeoutError(
                command=command_tuple,
                timeout=timeout,
            ) from exc
        except OSError as exc:
            raise ProcessStartError(
                command=command_tuple,
                error=exc,
            ) from exc

        return ProcessResult(
            command=command_tuple,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
