from dataclasses import replace

import pytest

from checks.audio_loss.event_reducer import (
    AudioLossEventReducer,
    AudioLossEventTransitionType,
)
from models.audio import AudioTrackPresence, SilenceInterval
from models.audio_loss import (
    AudioLossCause,
    AudioLossDetectionResult,
    AudioLossEventStatus,
    AudioLossSignal,
)
from policies.audio_loss import AudioLossAlertPolicy
from tests.factories.hls import make_segment


def detection(segment, signal, *intervals, checked=True):
    presence = {
        AudioLossSignal.MISSING: AudioTrackPresence.ABSENT,
        AudioLossSignal.UNKNOWN: AudioTrackPresence.UNKNOWN,
    }.get(signal, AudioTrackPresence.PRESENT)
    return AudioLossDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        track_presence=presence,
        signal=signal,
        checked=checked,
        silence_intervals=list(intervals),
    )


def reduce(reducer, segment, signal, *intervals, open_event=None, checked=True):
    return reducer.reduce(
        open_event=open_event,
        segment=segment,
        result=detection(segment, signal, *intervals, checked=checked),
    )


def test_audible_without_open_event_marks_segment_committed():
    transitions = reduce(
        AudioLossEventReducer(
            storage_id="storage-1", external_stream_id="channel-01"
        ),
        make_segment(10),
        AudioLossSignal.AUDIBLE,
    )

    assert len(transitions) == 1
    assert transitions[0].type is AudioLossEventTransitionType.MARK_COMMITTED
    assert transitions[0].commits_segment is True


def test_partial_silence_uses_offsets_and_resolves_in_same_segment():
    segment = make_segment(10, duration=6.0)
    transitions = reduce(
        AudioLossEventReducer(
            storage_id="storage-1", external_stream_id="channel-01"
        ),
        segment,
        AudioLossSignal.SILENT,
        SilenceInterval(1.25, 3.75),
    )

    transition = transitions[0]
    assert transition.type is AudioLossEventTransitionType.RESOLVE
    assert transition.reason == "audio_returned"
    assert transition.commits_segment is True
    assert transition.event.stream_id == "channel-01"
    assert transition.event.duration == pytest.approx(2.5)
    assert transition.event.primary_cause is AudioLossCause.CONTINUOUS_SILENCE
    assert transition.event.status is AudioLossEventStatus.RESOLVED


def test_missing_audio_counts_whole_segment_and_stays_open():
    segment = make_segment(10, duration=2.0)
    transition = reduce(
        AudioLossEventReducer(
            storage_id="storage-1", external_stream_id="channel-01"
        ),
        segment,
        AudioLossSignal.MISSING,
    )[0]

    assert transition.type is AudioLossEventTransitionType.PERSIST_OPEN
    assert transition.event.stream_id == "channel-01"
    assert transition.event.duration == pytest.approx(2.0)
    assert transition.event.start_offset == 0.0
    assert transition.event.end_offset == 2.0
    assert transition.event.primary_cause is AudioLossCause.AUDIO_STREAM_MISSING


def test_fifteen_two_second_segments_form_one_thirty_second_event():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    current = None
    event_ids = set()

    for sequence in range(100, 115):
        transition = reduce(
            reducer,
            make_segment(sequence, duration=2.0),
            AudioLossSignal.MISSING,
            open_event=current,
        )[0]
        assert transition.type is AudioLossEventTransitionType.PERSIST_OPEN
        current = transition.event
        event_ids.add(current.event_id)

    assert len(event_ids) == 1
    assert current.duration == pytest.approx(30.0)
    assert current.affected_segment_count == 15
    assert AudioLossAlertPolicy().should_alert(current.duration) is True


def test_missing_to_silence_transition_keeps_one_event_and_both_causes():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    first = make_segment(10, duration=20.0)
    opened = reduce(
        reducer,
        first,
        AudioLossSignal.MISSING,
    )[0].event
    event_id = opened.event_id

    transition = reduce(
        reducer,
        make_segment(11, duration=15.0),
        AudioLossSignal.SILENT,
        SilenceInterval(0.0, 15.0),
        open_event=opened,
    )[0]

    assert transition.event.event_id == event_id
    assert transition.event.stream_id == "channel-01"
    assert transition.event.duration == pytest.approx(35.0)
    assert transition.event.primary_cause is AudioLossCause.AUDIO_STREAM_MISSING
    assert transition.event.causes_seen == [
        AudioLossCause.AUDIO_STREAM_MISSING,
        AudioLossCause.CONTINUOUS_SILENCE,
    ]


def test_audible_recovery_resolves_with_final_duration():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    opened = reduce(
        reducer,
        make_segment(10, duration=6.0),
        AudioLossSignal.SILENT,
        SilenceInterval(2.0, 6.0),
    )[0].event

    transition = reduce(
        reducer,
        make_segment(11, duration=6.0),
        AudioLossSignal.AUDIBLE,
        open_event=opened,
    )[0]

    assert transition.type is AudioLossEventTransitionType.RESOLVE
    assert transition.reason == "audio_returned"
    assert transition.event.stream_id == "channel-01"
    assert transition.event.duration == pytest.approx(4.0)
    assert transition.event.resolution_reason == "audio_returned"
    assert transition.commits_segment is True


def test_unknown_preserves_loaded_event_without_transition_or_mutation():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    opened = reduce(
        reducer,
        make_segment(10, duration=2.0),
        AudioLossSignal.MISSING,
    )[0].event
    original = replace(
        opened,
        causes_seen=list(opened.causes_seen),
    )

    transitions = reduce(
        reducer,
        make_segment(11, duration=2.0),
        AudioLossSignal.UNKNOWN,
        open_event=opened,
        checked=False,
    )

    assert transitions == []
    assert opened == original


@pytest.mark.parametrize(
    "changed_segment",
    [
        make_segment(12, duration=2.0),
        make_segment(11, duration=2.0, discontinuity_sequence=1),
    ],
)
def test_sequence_gap_or_discontinuity_does_not_join_event(changed_segment):
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    opened = reduce(
        reducer,
        make_segment(10, duration=2.0),
        AudioLossSignal.MISSING,
    )[0].event

    transitions = reduce(
        reducer,
        changed_segment,
        AudioLossSignal.MISSING,
        open_event=opened,
    )

    assert [item.type for item in transitions] == [
        AudioLossEventTransitionType.RESOLVE,
        AudioLossEventTransitionType.PERSIST_OPEN,
    ]
    assert transitions[0].reason == "observation_gap"
    assert transitions[1].event.event_id != opened.event_id
    assert transitions[1].event.stream_id == "channel-01"


@pytest.mark.parametrize(
    ("field", "old", "new"),
    [
        ("timeline_generation", 1, 2),
        ("media_revision", "revision-a", "revision-b"),
    ],
)
def test_timeline_reset_or_replacement_does_not_join_event(field, old, new):
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    first = make_segment(10, duration=2.0)
    setattr(first, field, old)
    opened = reduce(reducer, first, AudioLossSignal.MISSING)[0].event
    replacement = make_segment(10, duration=2.0)
    setattr(replacement, field, new)

    transitions = reduce(
        reducer,
        replacement,
        AudioLossSignal.MISSING,
        open_event=opened,
    )

    assert transitions[0].type is AudioLossEventTransitionType.RESOLVE
    assert transitions[0].reason == "observation_gap"
    assert transitions[1].type is AudioLossEventTransitionType.PERSIST_OPEN
    assert transitions[1].event.event_id != opened.event_id
    assert transitions[1].event.stream_id == "channel-01"


def test_same_segment_overlap_only_adds_new_duration():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    segment = make_segment(10, duration=6.0)
    opened = reduce(
        reducer,
        segment,
        AudioLossSignal.SILENT,
        SilenceInterval(1.0, 6.0),
    )[0].event

    transition = reduce(
        reducer,
        segment,
        AudioLossSignal.SILENT,
        SilenceInterval(4.0, 6.0),
        open_event=opened,
    )[0]

    assert transition.event.duration == pytest.approx(5.0)
    assert transition.event.stream_id == "channel-01"


def test_reducer_rejects_negative_boundary_tolerance():
    with pytest.raises(ValueError, match="boundary_tolerance"):
        AudioLossEventReducer(
            storage_id="storage-1",
            external_stream_id="channel-01",
            boundary_tolerance=-0.01,
        )


def test_unknown_sequence_never_claims_recovery_or_bridges_observation_gap():
    reducer = AudioLossEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    opened = reduce(
        reducer,
        make_segment(10, duration=2.0),
        AudioLossSignal.MISSING,
    )[0].event

    assert reduce(
        reducer,
        make_segment(11, duration=2.0),
        AudioLossSignal.UNKNOWN,
        open_event=opened,
        checked=False,
    ) == []

    transitions = reduce(
        reducer,
        make_segment(12, duration=2.0),
        AudioLossSignal.MISSING,
        open_event=opened,
    )

    assert transitions[0].reason == "observation_gap"
    assert transitions[0].reason != "audio_returned"
    assert transitions[1].event.event_id != opened.event_id
    assert transitions[1].event.stream_id == "channel-01"


def test_multiple_silence_intervals_form_distinct_resolved_events():
    segment = make_segment(10, duration=6.0)

    transitions = reduce(
        AudioLossEventReducer(
            storage_id="storage-1", external_stream_id="channel-01"
        ),
        segment,
        AudioLossSignal.SILENT,
        SilenceInterval(1.0, 2.0),
        SilenceInterval(4.0, 5.5),
    )

    assert [item.type for item in transitions] == [
        AudioLossEventTransitionType.RESOLVE,
        AudioLossEventTransitionType.RESOLVE,
    ]
    assert [item.event.duration for item in transitions] == [1.0, 1.5]
    assert transitions[0].event.event_id != transitions[1].event.event_id
    assert transitions[0].commits_segment is False
    assert transitions[1].commits_segment is True
    assert transitions[0].event.stream_id == "channel-01"


def test_audio_reducer_normalizes_legacy_open_event_stream_id():
    reducer = AudioLossEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
    )
    from models.audio_loss import AudioLossLiveEvent
    legacy_event = AudioLossLiveEvent(
        event_id="legacy-audio-id",
        stream_id="legacy-hash-id",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        start_sequence=10,
        end_sequence=10,
        start_offset=0.0,
        end_offset=2.0,
        start_program_time=None,
        end_program_time=None,
        duration=2.0,
        last_segment_duration=2.0,
        primary_cause=AudioLossCause.AUDIO_STREAM_MISSING,
        causes_seen=[AudioLossCause.AUDIO_STREAM_MISSING],
        affected_segment_count=1,
    )
    segment = make_segment(11, duration=2.0)
    transitions = reduce(
        reducer,
        segment,
        AudioLossSignal.MISSING,
        open_event=legacy_event,
    )
    assert len(transitions) == 1
    assert transitions[0].event.stream_id == "channel-01"
    assert transitions[0].event.event_id == "legacy-audio-id"
