from dataclasses import dataclass

import pytest

from core.process_runner import ProcessStartError, ProcessTimeoutError
from media.input_resolver import ResolvedMediaInput
from models.analysis import AnalysisRequirement, AnalysisResourceClass
from models.audio import AudioTrackHint, AudioTrackPresence
from models.segment import SegmentEncryption
from models.rendition import MediaRenditionKind
from profiles.audio_realtime import AudioRealtimeProfile
from profiles.audio_realtime.command_builder import AudioRealtimeCommandBuilder
from profiles.audio_realtime.parser import SilencedetectParser
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
        self.calls.append({"command": command, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def segment():
    return make_segment(100, duration=6.0)


def audio_result(profile, segment):
    return profile.analyze(segment).require_audio_realtime()


def silence_intervals(result):
    return result.require_output(AnalysisRequirement.SILENCE_INTERVALS, tuple)


def test_profile_contract_and_single_silencedetect_command(segment):
    runner = FakeProcessRunner()
    profile = AudioRealtimeProfile(
        threshold_dbfs=-60.0,
        detector_minimum_duration=0.1,
        timeout=7.0,
        runner=runner,
    )

    result = audio_result(profile, segment)

    assert profile.resource_class is AnalysisResourceClass.AUDIO_DECODE
    assert profile.provides == {AnalysisRequirement.SILENCE_INTERVALS}
    assert result.checked is True
    assert result.presence is AudioTrackPresence.PRESENT
    call = runner.calls[0]
    command = call["command"]
    assert len(runner.calls) == 1
    assert call["timeout"] == 7.0
    assert command[0] == "ffmpeg"
    assert "-xerror" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert "-vn" in command
    assert segment.uri in command
    assert "silencedetect=noise=-60dB:d=0.1:mono=0" in command


def test_command_builder_assembles_filters_into_one_process():
    command = AudioRealtimeCommandBuilder().build(
        media_input=ResolvedMediaInput("https://media/segment.ts"),
        filter_expressions=(
            "silencedetect=noise=-60dB:d=0.1:mono=0",
            "ebur128=metadata=1",
        ),
    )

    assert command.count("ffmpeg") == 1
    assert command.count("-af") == 1
    assert command[command.index("-af") + 1] == (
        "silencedetect=noise=-60dB:d=0.1:mono=0,ebur128=metadata=1"
    )


def test_command_builder_maps_configured_audio_track():
    command = AudioRealtimeCommandBuilder(track_index=2).build(
        media_input=ResolvedMediaInput("https://media/segment.ts"),
        filter_expressions=("silencedetect=d=0.1",),
    )

    assert command[command.index("-map") + 1] == "0:a:2"


def test_audio_rendition_always_maps_its_first_track(segment):
    segment.rendition_kind = MediaRenditionKind.AUDIO
    segment.has_video = False
    runner = FakeProcessRunner()
    profile = AudioRealtimeProfile(
        track_index=2,
        runner=runner,
    )

    result = audio_result(profile, segment)

    assert result.checked is True
    command = runner.calls[0]["command"]
    assert command[command.index("-map") + 1] == "0:a:0"


def test_command_builder_rejects_negative_track_index():
    with pytest.raises(ValueError, match="track_index"):
        AudioRealtimeCommandBuilder(track_index=-1)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ordinary ffmpeg output", []),
        (
            "silence_start: 1.2\nsilence_end: 3.7 | silence_duration: 2.5",
            [(1.2, 3.7)],
        ),
        (
            "\n".join(
                [
                    "silence_start: 0",
                    "silence_end: 1",
                    "silence_start: 2.1",
                    "silence_end: 3.8",
                ]
            ),
            [(0.0, 1.0), (2.1, 3.8)],
        ),
        ("silence_start: 4.25", [(4.25, 6.0)]),
        ("silence_start: 4 silence_end: 7.5", [(4.0, 6.0)]),
        ("silence_start: -0.2 silence_end: 1e0", [(0.0, 1.0)]),
        ("silence_start: 3 silence_end: 3", []),
        ("silence_end: 2", []),
    ],
)
def test_silencedetect_parser_clamps_intervals(segment, output, expected):
    intervals = SilencedetectParser().parse(
        ffmpeg_output=output,
        segment=segment,
    )

    assert [(interval.start, interval.end) for interval in intervals] == expected


@pytest.mark.parametrize(
    ("threshold", "duration", "message"),
    [
        (1.0, 0.1, "threshold_dbfs"),
        (float("nan"), 0.1, "threshold_dbfs"),
        (-60.0, 0.0, "minimum_duration"),
    ],
)
def test_silencedetect_parser_rejects_invalid_config(
    threshold,
    duration,
    message,
):
    with pytest.raises(ValueError, match=message):
        SilencedetectParser(
            threshold_dbfs=threshold,
            minimum_duration=duration,
        )


def test_manifest_absent_is_checked_without_ffmpeg(segment):
    segment.audio_track_hint = AudioTrackHint.ABSENT
    runner = FakeProcessRunner()

    result = audio_result(AudioRealtimeProfile(runner=runner), segment)

    assert result.checked is True
    assert result.presence is AudioTrackPresence.ABSENT
    assert result.retryable is False
    assert silence_intervals(result) == ()
    assert runner.calls == []


def test_strict_map_missing_audio_is_absent(segment):
    result = audio_result(
        AudioRealtimeProfile(
            runner=FakeProcessRunner(
                result=FakeProcessResult(
                    returncode=1,
                    stderr="Stream map '0:a:0' matches no streams.",
                )
            )
        ),
        segment,
    )

    assert result.checked is True
    assert result.presence is AudioTrackPresence.ABSENT
    assert silence_intervals(result) == ()


def test_external_audio_is_unknown_and_not_supported_for_admission(segment):
    segment.audio_track_hint = AudioTrackHint.EXTERNAL
    runner = FakeProcessRunner()
    profile = AudioRealtimeProfile(runner=runner)

    result = audio_result(profile, segment)

    assert profile.supports_segment(segment) is False
    assert result.checked is False
    assert result.presence is AudioTrackPresence.UNKNOWN
    assert result.retryable is False
    assert "External HLS audio rendition" in result.error
    assert runner.calls == []


def test_gap_is_unknown_without_ffmpeg(segment):
    segment.gap = True
    runner = FakeProcessRunner()

    result = audio_result(AudioRealtimeProfile(runner=runner), segment)

    assert result.checked is False
    assert result.presence is AudioTrackPresence.UNKNOWN
    assert result.retryable is False
    assert "EXT-X-GAP" in result.error
    assert runner.calls == []


def test_unsupported_media_input_is_unknown_and_terminal(segment):
    segment.encryption = SegmentEncryption(method="AES-128")
    runner = FakeProcessRunner()

    result = audio_result(AudioRealtimeProfile(runner=runner), segment)

    assert result.checked is False
    assert result.presence is AudioTrackPresence.UNKNOWN
    assert result.retryable is False
    assert "method=AES-128" in result.error
    assert runner.calls == []


@pytest.mark.parametrize(
    "error",
    [
        ProcessTimeoutError(command=("ffmpeg",), timeout=7.0),
        ProcessStartError(command=("ffmpeg",), error=OSError("not found")),
    ],
)
def test_process_failure_is_retryable_unknown(segment, error):
    result = audio_result(
        AudioRealtimeProfile(runner=FakeProcessRunner(error=error)),
        segment,
    )

    assert result.checked is False
    assert result.presence is AudioTrackPresence.UNKNOWN
    assert result.retryable is True
    assert result.error
    assert result.timed_out is isinstance(error, ProcessTimeoutError)


def test_decode_failure_is_retryable_unknown(segment):
    result = audio_result(
        AudioRealtimeProfile(
            runner=FakeProcessRunner(
                result=FakeProcessResult(
                    returncode=1,
                    stderr="Invalid data found when processing input",
                )
            )
        ),
        segment,
    )

    assert result.checked is False
    assert result.presence is AudioTrackPresence.UNKNOWN
    assert result.retryable is True
    assert result.error == "Invalid data found when processing input"


def test_profile_rejects_video_requirement(segment):
    with pytest.raises(ValueError, match="Unsupported audio requirements"):
        AudioRealtimeProfile(runner=FakeProcessRunner()).analyze(
            segment,
            requirements=frozenset({AnalysisRequirement.BLACK_INTERVALS}),
        )
