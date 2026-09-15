from dataclasses import dataclass
from pathlib import Path

import pytest

from core.process_runner import (
    ProcessStartError,
    ProcessTimeoutError,
)
from models.analysis import AnalysisRequirement
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from detectors.macroblocking import MacroblockingAnalyzer
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
        if "rawvideo" in command:
            Path(command[-1]).write_bytes(bytes([100] * (2 * 32 * 32)))
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
    filter_graph = command[command.index("-filter_complex") + 1]
    assert filter_graph == (
        "[0:v:0]split=2[analysis][sample_source];"
        "[analysis]blackdetect=d=0:pix_th=0.1:pic_th=0.98,"
        "freezedetect=n=-60dB:d=0.2[analyzed];"
        "[sample_source]scale=32:32,format=gray[samples]"
    )
    assert [(item.start, item.end) for item in black_intervals(result)] == [
        (1.0, 2.0)
    ]
    assert [(item.start, item.end) for item in freeze_intervals(result)] == [
        (2.5, 4.5)
    ]
    assert result.diagnostics == {
        "video_freeze_raw_interval_total": 1,
        "video_freeze_raw_seconds_total": 2.0,
        "video_freeze_black_overlap_seconds_total": 0.0,
        "video_freeze_boundary_fingerprint_total": 0,
    }


def test_profile_can_request_only_freeze_observation(segment):
    runner = FakeProcessRunner()
    profile = VideoRealtimeProfile(runner=runner)

    result = video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.FREEZE_INTERVALS}),
    )

    command = runner.calls[0]["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "blackdetect=d=0:pix_th=0.1:pic_th=0.98" in filter_graph
    assert "freezedetect=n=-60dB:d=0.2" in filter_graph
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


def test_freeze_sampling_command_uses_one_input_decode(tmp_path):
    raw_path = tmp_path / "frames.raw"
    command = VideoRealtimeCommandBuilder().build(
        media_input=ResolvedMediaInput("https://media/segment.ts"),
        filter_expressions=("blackdetect=d=0", "freezedetect=d=0.2"),
        boundary_raw_output=raw_path,
    )

    assert command.count("ffmpeg") == 1
    assert command.count("-i") == 1
    assert command.count("-filter_complex") == 1
    assert str(raw_path) == command[-1]


def test_macroblocking_sampling_adds_branch_to_same_decode(tmp_path):
    boundary_path = tmp_path / "boundary.raw"
    macroblocking_path = tmp_path / "macroblocking.raw"
    command = VideoRealtimeCommandBuilder().build(
        media_input=ResolvedMediaInput("https://media/segment.ts"),
        filter_expressions=("blackdetect=d=0", "freezedetect=d=0.2"),
        boundary_raw_output=boundary_path,
        macroblocking_raw_output=macroblocking_path,
        macroblocking_width=64,
        macroblocking_height=36,
        macroblocking_sampling_fps=1.0,
    )

    assert command.count("ffmpeg") == 1
    assert command.count("-i") == 1
    graph = command[command.index("-filter_complex") + 1]
    assert "split=3" in graph
    assert "blackdetect=d=0,freezedetect=d=0.2" in graph
    assert "scale=32:32,format=gray[samples]" in graph
    assert "fps=1,scale=64:36:flags=area,format=gray" in graph
    assert command[command.index("-frames:v") + 1] == "64"
    assert str(boundary_path) in command
    assert str(macroblocking_path) in command


def test_disabled_macroblocking_keeps_legacy_command_shape(segment):
    runner = FakeProcessRunner()
    profile = VideoRealtimeProfile(runner=runner)
    video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.BLACK_INTERVALS}),
    )
    command = runner.calls[0]["command"]
    assert "-vf" in command
    assert "macroblocking_samples" not in command


def test_macroblocking_only_request_still_uses_one_ffmpeg_process(segment):
    class MacroSampleRunner(FakeProcessRunner):
        def run(self, command, *, timeout):
            self.calls.append({"command": command, "timeout": timeout})
            Path(command[-1]).write_bytes(bytes(range(32)) * 24)
            return self.result

    runner = MacroSampleRunner()
    analyzer = MacroblockingAnalyzer(
        MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            analysis_width=32,
            analysis_height=24,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        )
    )
    profile = VideoRealtimeProfile(
        runner=runner,
        enable_macroblocking=True,
        macroblocking_analyzer=analyzer,
    )

    result = video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.MACROBLOCKING_OBSERVATIONS}),
    )

    observations = result.require_output(
        AnalysisRequirement.MACROBLOCKING_OBSERVATIONS,
        tuple,
    )
    assert result.checked is True
    assert len(observations) == 1
    assert len(runner.calls) == 1
    command = runner.calls[0]["command"]
    assert command.count("-i") == 1
    assert "null[analyzed]" in command[command.index("-filter_complex") + 1]


def test_macroblocking_does_not_change_black_or_freeze_outputs(segment):
    class CombinedSampleRunner(FakeProcessRunner):
        def run(self, command, *, timeout):
            self.calls.append({"command": command, "timeout": timeout})
            outputs = [
                Path(command[index + 1])
                for index, value in enumerate(command)
                if value == "rawvideo"
            ]
            outputs[0].write_bytes(bytes([90]) * (2 * 32 * 32))
            outputs[1].write_bytes(bytes(range(32)) * 24)
            return self.result

    runner = CombinedSampleRunner(
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
    analyzer = MacroblockingAnalyzer(
        MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            analysis_width=32,
            analysis_height=24,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        )
    )
    profile = VideoRealtimeProfile(
        runner=runner,
        enable_macroblocking=True,
        macroblocking_analyzer=analyzer,
    )

    result = video_result(
        profile,
        segment,
        frozenset(
            {
                AnalysisRequirement.BLACK_INTERVALS,
                AnalysisRequirement.FREEZE_INTERVALS,
                AnalysisRequirement.MACROBLOCKING_OBSERVATIONS,
            }
        ),
    )

    assert [(item.start, item.end) for item in black_intervals(result)] == [
        (1.0, 2.0)
    ]
    assert [(item.start, item.end) for item in freeze_intervals(result)] == [
        (2.5, 4.5)
    ]
    assert len(runner.calls) == 1


def test_macroblocking_workspace_is_cleaned_after_timeout(segment):
    class TimeoutAfterWriteRunner(FakeProcessRunner):
        raw_path: Path | None = None

        def run(self, command, *, timeout):
            self.calls.append({"command": command, "timeout": timeout})
            self.raw_path = Path(command[-1])
            self.raw_path.write_bytes(bytes(range(32)) * 24)
            raise ProcessTimeoutError(command, timeout)

    runner = TimeoutAfterWriteRunner()
    analyzer = MacroblockingAnalyzer(
        MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            analysis_width=32,
            analysis_height=24,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        )
    )
    profile = VideoRealtimeProfile(
        runner=runner,
        enable_macroblocking=True,
        macroblocking_analyzer=analyzer,
    )

    result = video_result(
        profile,
        segment,
        frozenset({AnalysisRequirement.MACROBLOCKING_OBSERVATIONS}),
    )

    assert result.checked is False
    assert result.timed_out is True
    assert runner.raw_path is not None
    assert not runner.raw_path.exists()


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
