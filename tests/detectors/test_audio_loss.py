import pytest

from detectors.audio_loss import AudioLossDetector
from models.analysis import (
    AnalysisRequirement,
    AudioRealtimeAnalysis,
    SegmentAnalysisBundle,
)
from models.audio import AudioTrackPresence, SilenceInterval
from models.audio_loss import AudioLossSignal
from tests.factories.hls import make_segment


def bundle(*, checked=True, presence=AudioTrackPresence.PRESENT, intervals=(), **kw):
    return SegmentAnalysisBundle(
        profile_name="audio_realtime",
        audio_realtime=AudioRealtimeAnalysis(
            checked=checked,
            presence=presence,
            outputs={AnalysisRequirement.SILENCE_INTERVALS: tuple(intervals)},
            **kw,
        ),
    )


@pytest.mark.parametrize(
    ("presence", "intervals", "signal"),
    [
        (AudioTrackPresence.PRESENT, (), AudioLossSignal.AUDIBLE),
        (
            AudioTrackPresence.PRESENT,
            (SilenceInterval(1.0, 2.0),),
            AudioLossSignal.SILENT,
        ),
        (AudioTrackPresence.ABSENT, (), AudioLossSignal.MISSING),
    ],
)
def test_detector_normalizes_checked_analysis(presence, intervals, signal):
    result = AudioLossDetector().detect(
        segment=make_segment(10),
        analysis=bundle(presence=presence, intervals=intervals),
    )

    assert result.checked is True
    assert result.signal is signal
    assert result.track_presence is presence
    assert result.silence_intervals == list(intervals)


def test_detector_preserves_unknown_failure_contract():
    result = AudioLossDetector().detect(
        segment=make_segment(10),
        analysis=bundle(
            checked=False,
            presence=AudioTrackPresence.UNKNOWN,
            error="ffmpeg timeout",
            retryable=True,
            timed_out=True,
        ),
    )

    assert result.checked is False
    assert result.signal is AudioLossSignal.UNKNOWN
    assert result.track_presence is AudioTrackPresence.UNKNOWN
    assert result.error == "ffmpeg timeout"
    assert result.retryable is True


def test_detector_rejects_invalid_silence_output():
    with pytest.raises(TypeError, match="silence_intervals"):
        AudioLossDetector().detect(
            segment=make_segment(10),
            analysis=bundle(intervals=("invalid",)),
        )
