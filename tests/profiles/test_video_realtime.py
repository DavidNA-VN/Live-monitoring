from dataclasses import dataclass

import pytest

from core.process_runner import (
    ProcessStartError,
    ProcessTimeoutError,
)
from models.analysis import AnalysisRequirement
from media.input_resolver import ResolvedMediaInput
from models.segment import SegmentEncryption
from profiles.video_realtime import VideoRealtimeProfile
from profiles.video_realtime.command_builder import (
    VideoRealtimeCommandBuilder,
)
from tests.factories.hls import make_segment


@dataclass
class FakeProcessResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class FakeProcessRunner:
    def __init__(self, *, result=None, error=None):
        self.result = result or FakeProcessResult()
        self.error = error
        self.calls = []

    def run(self, command, *, timeout):
        self.calls.append(
            {"command": command, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def segment():
    return make_segment(100, duration=6.0)


def video_result(profile, segment, requirements=None):
    return profile.analyze(
        segment,
        requirements=requirements,
    ).require_video_realtime()


def black_intervals(result):
    return result.require_output(AnalysisRequirement.BLACK_INTERVALS, tuple)


def freeze_intervals(result):
    return result.require_output(AnalysisRequirement.FREEZE_INTERVALS, tuple)


def test_profile_builds_single_blackdetect_command(segment):
    runner = FakeProcessRunner()
    profile = VideoRealtimeProfile(
        pix_th=0.10,
        pic_th=0.98,
        timeout=7.0,
        runner=runner,
    )

    video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.BLACK_INTERVALS}),
    )

    call = runner.calls[0]
    command = call["command"]
    assert len(runner.calls) == 1
    assert call["timeout"] == 7.0
    assert command[0] == "ffmpeg"
    assert "-nostdin" in command
    assert "0:v:0" in command
    assert "-an" in command
    assert segment.uri in command
    assert (
        "blackdetect=d=0:pix_th=0.1:pic_th=0.98"
        in command
    )
    assert "freezedetect=" not in command


def test_profile_builds_black_and_freeze_filters_in_one_process(segment):
    runner = FakeProcessRunner(
        result=FakeProcessResult(
            stderr="\n".join(
                (
                    "black_start:1 black_end:2",
                    "lavfi.freezedetect.freeze_start: 2.5",
                    "lavfi.freezedetect.freeze_duration: 2",
                    "lavfi.freezedetect.freeze_end: 4.5",
                )
            )
        )
    )
    profile = VideoRealtimeProfile(runner=runner)

    result = video_result(
        profile,
        segment,
        frozenset(
            {
                AnalysisRequirement.BLACK_INTERVALS,
                AnalysisRequirement.FREEZE_INTERVALS,
            }
        ),
    )

    assert len(runner.calls) == 1
    command = runner.calls[0]["command"]
    filter_graph = command[command.index("-vf") + 1]
    assert filter_graph == (
        "blackdetect=d=0:pix_th=0.1:pic_th=0.98,"
        "freezedetect=n=-60dB:d=0.2"
    )
    assert [(item.start, item.end) for item in black_intervals(result)] == [
        (1.0, 2.0)
    ]
    assert [(item.start, item.end) for item in freeze_intervals(result)] == [
        (2.5, 4.5)
    ]


def test_profile_can_request_only_freeze_observation(segment):
    runner = FakeProcessRunner()
    profile = VideoRealtimeProfile(runner=runner)

    result = video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.FREEZE_INTERVALS}),
    )

    command = runner.calls[0]["command"]
    filter_graph = command[command.index("-vf") + 1]
    assert filter_graph == "freezedetect=n=-60dB:d=0.2"
    assert freeze_intervals(result) == ()
    with pytest.raises(ValueError, match="black_intervals"):
        black_intervals(result)


def test_command_builder_assembles_filters_into_one_decode_command():
    command = VideoRealtimeCommandBuilder().build(
        media_input=ResolvedMediaInput("https://media/segment.ts"),
        filter_expressions=("blackdetect=d=0", "freezedetect=d=2"),
    )

    assert command.count("ffmpeg") == 1
    assert command.count("-vf") == 1
    assert command[command.index("-vf") + 1] == (
        "blackdetect=d=0,freezedetect=d=2"
    )


def test_gap_is_terminal_without_ffmpeg(segment):
    segment.gap = True
    runner = FakeProcessRunner()

    result = video_result(
        VideoRealtimeProfile(runner=runner),
        segment,
    )

    assert result.checked is False
    assert result.retryable is False
    assert "EXT-X-GAP" in result.error
    assert runner.calls == []


def test_unsupported_media_input_is_not_retryable(segment):
    segment.encryption = SegmentEncryption(method="AES-128")
    runner = FakeProcessRunner()

    result = video_result(
        VideoRealtimeProfile(runner=runner),
        segment,
    )

    assert result.checked is False
    assert result.retryable is False
    assert "method=AES-128" in result.error
    assert runner.calls == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ordinary ffmpeg output", []),
        (
            "[blackdetect] black_start:1.2 "
            "black_end:3.7 black_duration:2.5",
            [(1.2, 3.7)],
        ),
        (
            "\n".join(
                [
                    "black_start:0.4 black_end:1.0",
                    "black_start:2.1 black_end:3.8",
                    "black_start:5.0 black_end:5.5",
                ]
            ),
            [(0.4, 1.0), (2.1, 3.8), (5.0, 5.5)],
        ),
        ("black_start:4.25", [(4.25, 6.0)]),
        ("black_start:4.0 black_end:7.5", [(4.0, 6.0)]),
        ("black_start:3.0 black_end:3.0", []),
    ],
)
def test_profile_parses_black_intervals(segment, output, expected):
    result = video_result(
        VideoRealtimeProfile(
            runner=FakeProcessRunner(
                result=FakeProcessResult(stderr=output)
            )
        ),
        segment,
    )

    assert result.checked is True
    assert [
        (interval.start, interval.end)
        for interval in black_intervals(result)
    ] == expected


@pytest.mark.parametrize(
    "error",
    [
        ProcessTimeoutError(
            command=("ffmpeg",),
            timeout=7.0,
        ),
        ProcessStartError(
            command=("ffmpeg",),
            error=OSError("not found"),
        ),
    ],
)
def test_process_failure_maps_to_retryable_analysis(
    segment,
    error,
):
    result = video_result(
        VideoRealtimeProfile(
            runner=FakeProcessRunner(error=error)
        ),
        segment,
    )

    assert result.checked is False
    assert result.retryable is True
    assert result.error
    assert result.timed_out is isinstance(error, ProcessTimeoutError)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("Invalid data found", "Invalid data found"),
        ("", "FFmpeg exited with code 1"),
    ],
)
def test_nonzero_ffmpeg_result_maps_error(
    segment,
    stderr,
    expected,
):
    result = video_result(
        VideoRealtimeProfile(
            runner=FakeProcessRunner(
                result=FakeProcessResult(
                    returncode=1,
                    stderr=stderr,
                )
            )
        ),
        segment,
    )

    assert result.checked is False
    assert result.error == expected
