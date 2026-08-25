import pytest

from checks.audio_loss.processor import AudioLossSegmentProcessor
from core.segment_processor import SegmentProcessOutcome
from models.analysis import (
    AnalysisRequirement,
    AudioRealtimeAnalysis,
    SegmentAnalysisBundle,
)
from models.audio import AudioTrackHint, AudioTrackPresence, SilenceInterval
from models.audio_loss import AudioLossDetectionResult, AudioLossSignal
from tests.factories.hls import make_segment


class FakeEventStore:
    def __init__(self):
        self.applied = []

    def apply(self, *, segment, result):
        self.applied.append((segment, result))


def bundle(*, checked=True, error=None, retryable=True, presence=None, intervals=()):
    effective_presence = presence or (
        AudioTrackPresence.PRESENT if checked else AudioTrackPresence.UNKNOWN
    )
    return SegmentAnalysisBundle(
        profile_name="audio_realtime",
        audio_realtime=AudioRealtimeAnalysis(
            checked=checked,
            presence=effective_presence,
            error=error,
            retryable=retryable,
            outputs={AnalysisRequirement.SILENCE_INTERVALS: tuple(intervals)},
        ),
    )


def processor(event_store=None):
    return AudioLossSegmentProcessor(event_store or FakeEventStore())


def test_processor_declares_audio_profile_requirement():
    instance = processor()

    assert instance.name == "audio_loss"
    assert instance.analysis_profile == "audio_realtime"
    assert instance.requirements == {AnalysisRequirement.SILENCE_INTERVALS}


def test_processor_excludes_external_audio_rendition():
    segment = make_segment(10)
    segment.audio_track_hint = AudioTrackHint.EXTERNAL

    assert processor().supports_segment(segment) is False


@pytest.mark.parametrize("retryable", [True, False])
def test_unknown_analysis_follows_retry_contract(retryable):
    outcome = processor().process(
        make_segment(10),
        bundle(
            checked=False,
            error="audio analysis failed",
            retryable=retryable,
        ),
    )

    assert outcome.success is False
    assert outcome.retryable is retryable
    assert outcome.error == "audio analysis failed"


def test_checked_silence_is_successful_payload():
    outcome = processor().process(
        make_segment(10),
        bundle(intervals=(SilenceInterval(1.0, 2.0),)),
    )

    assert outcome.success is True
    assert outcome.payload.signal is AudioLossSignal.SILENT


def test_checked_missing_is_successful_payload():
    outcome = processor().process(
        make_segment(10),
        bundle(presence=AudioTrackPresence.ABSENT),
    )

    assert outcome.success is True
    assert outcome.payload.signal is AudioLossSignal.MISSING


def test_commit_applies_detection_result():
    segment = make_segment(10)
    result = AudioLossDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        track_presence=AudioTrackPresence.PRESENT,
        signal=AudioLossSignal.AUDIBLE,
    )
    store = FakeEventStore()

    AudioLossSegmentProcessor(store).commit(
        segment,
        SegmentProcessOutcome.ok(result),
    )

    assert store.applied == [(segment, result)]


def test_commit_rejects_invalid_payload():
    with pytest.raises(RuntimeError, match="without detection result"):
        processor().commit(
            make_segment(10),
            SegmentProcessOutcome.ok("invalid"),
        )
