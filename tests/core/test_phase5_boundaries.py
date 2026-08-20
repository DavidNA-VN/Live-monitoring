from datetime import datetime, timezone

from checks.black_screen.alert_publisher import BlackAlertPublisher
from checks.black_screen.redis_scripts import (
    RELEASE_OWNED_LOCK as BLACK_RELEASE_LOCK,
    WRITE_EVENT_EVIDENCE,
)
from core.live_polling import LivePlaylistPoller, calculate_poll_interval
from core.redis_scripts import (
    COMPLETE_SEGMENT,
    PUBLISH_RUNTIME_HEALTH,
    RELEASE_OWNED_LOCK,
    RENEW_OWNED_LOCK,
)
from core.redis_keys import RedisKeyBuilder
from models.black_live import BlackLiveEvent
from models.stream import StreamIdentity
from tests.core.test_live_runtime_sliding_window import make_context
from tests.factories.hls import make_snapshot


class RecordingPipeline:
    def __init__(self):
        self.entries = []

    def xadd(self, key, fields, **_kwargs):
        self.entries.append((key, fields))

    def hincrby(self, *_args):
        pass

    def expire(self, *_args):
        pass


class RecordingAlertSink:
    def __init__(self):
        self.envelopes = []

    def append(self, _pipeline, envelope):
        self.envelopes.append(envelope)


def black_event():
    return BlackLiveEvent(
        event_id="event-1",
        stream_id="stream-1",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        start_sequence=10,
        end_sequence=11,
        start_offset=0.0,
        end_offset=6.0,
        start_program_time=datetime.now(timezone.utc),
        end_program_time=datetime.now(timezone.utc),
        duration=6.0,
        last_segment_duration=6.0,
    )


def test_black_alert_mapping_has_no_redis_dependency():
    pipeline = RecordingPipeline()
    publisher = BlackAlertPublisher(
        stream_id="stream-1",
        key_builder=RedisKeyBuilder(prefix="test"),
    )

    publisher.add_event(
        pipeline,
        event=black_event(),
        state="OPEN",
        reason="continuous_black",
    )

    key, fields = pipeline.entries[0]
    assert key == "test:alerts:outbox"
    assert fields["type"] == "BLACK_SCREEN"
    assert fields["state"] == "OPEN"
    assert fields["duration"] == "6.000000"
    assert fields["schema_version"] == "1.0"
    assert fields["category"] == "content"


def test_black_alert_publisher_accepts_replaceable_sink():
    sink = RecordingAlertSink()
    publisher = BlackAlertPublisher(
        stream_id="stream-1",
        key_builder=RedisKeyBuilder(prefix="unused"),
        alert_sink=sink,
    )

    publisher.add_event(
        object(),
        event=black_event(),
        state="OPEN",
        reason="continuous_black",
    )

    assert sink.envelopes[0].event_type == "BLACK_SCREEN"
    assert sink.envelopes[0].stream_id == "stream-1"


def test_live_poller_delegates_network_observation_to_injected_loader():
    snapshot = make_snapshot([100])
    expected = make_context(snapshot)
    calls = []

    def loader(**kwargs):
        calls.append(kwargs)
        return expected

    poller = LivePlaylistPoller(
        timeout=3.0,
        media_playlist_workers=2,
        request_headers={"Authorization": "token"},
        loader=loader,
    )
    stream = StreamIdentity("stream-1", "https://test/master.m3u8")

    assert poller.poll(stream) is expected
    assert calls[0]["playlist_timeout"] == 3.0
    assert calls[0]["media_playlist_workers"] == 2


def test_poll_interval_policy_is_pure():
    context = make_context(make_snapshot([100]))

    assert calculate_poll_interval(
        context,
        poll_factor=0.5,
        minimum=0.5,
        maximum=5.0,
        fallback=1.0,
    ) == 3.0


def test_lua_scripts_have_explicit_module_ownership():
    scripts = (
        RELEASE_OWNED_LOCK,
        RENEW_OWNED_LOCK,
        COMPLETE_SEGMENT,
        PUBLISH_RUNTIME_HEALTH,
        BLACK_RELEASE_LOCK,
        WRITE_EVENT_EVIDENCE,
    )

    assert all("redis.call" in script for script in scripts)
