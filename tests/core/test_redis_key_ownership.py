import pytest

from checks.audio_loss.redis_keys import AudioLossRedisKeys
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from core.redis_keys import (
    AlertRedisKeys,
    ControlRedisKeys,
    DesiredStateRedisKeys,
    ProcessingRedisKeys,
    PublicRuntimeRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
    WorkerRedisKeys,
)
from models.processing import SegmentProcessingIdentity


def test_key_spaces_share_prefix_but_keep_domain_ownership():
    namespace = RedisNamespace(":monitor:test:")
    processing = ProcessingRedisKeys(namespace)
    runtime = RuntimeRedisKeys(namespace)
    public_runtime = PublicRuntimeRedisKeys(namespace)
    worker = WorkerRedisKeys(namespace)
    desired = DesiredStateRedisKeys(namespace)
    alerts = AlertRedisKeys(namespace)
    control = ControlRedisKeys(namespace)
    black = BlackScreenRedisKeys(namespace)
    audio = AudioLossRedisKeys(namespace)

    assert processing.namespace is namespace
    assert runtime.namespace is namespace
    assert public_runtime.namespace is namespace
    assert worker.namespace is namespace
    assert desired.namespace is namespace
    assert alerts.namespace is namespace
    assert control.namespace is namespace
    assert black.namespace is namespace
    assert audio.namespace is namespace
    assert not hasattr(processing, "open_event")
    assert not hasattr(runtime, "segment_state")
    assert not hasattr(public_runtime, "health")
    assert not hasattr(worker, "health")
    assert not hasattr(desired, "health")
    assert not hasattr(alerts, "health")
    assert not hasattr(control, "segment_state")
    assert not hasattr(black, "outbox")
    assert not hasattr(audio, "outbox")


def test_core_key_schemas_are_stable():
    namespace = RedisNamespace("monitor:test")
    processing = ProcessingRedisKeys(namespace)
    runtime = RuntimeRedisKeys(namespace)
    alerts = AlertRedisKeys(namespace)
    control = ControlRedisKeys(namespace)
    identity = SegmentProcessingIdentity(
        storage_id="stream-1",
        check_name="black_screen",
        variant_stable_id="v720",
        timeline_generation=2,
        discontinuity_sequence=3,
        sequence=100,
        media_revision="revision-1",
    )

    assert processing.segment_state(identity) == (
        "monitor:test:stream:stream-1:check:black_screen:variant:v720:"
        "timeline:2:disc:3:segment:100:revision:revision-1:state"
    )
    assert runtime.active_variants("stream-1") == (
        "monitor:test:stream:stream-1:runtime:active-variants"
    )
    assert runtime.health("stream-1") == (
        "monitor:test:stream:stream-1:runtime:health"
    )
    assert runtime.metrics("stream-1") == (
        "monitor:test:stream:stream-1:runtime:metrics"
    )
    assert alerts.outbox() == "monitor:test:alerts:outbox"
    assert alerts.dead_letter() == "monitor:test:alerts:dead-letter"
    assert control.commands() == "monitor:test:monitoring:commands"
    assert control.command_results() == (
        "monitor:test:monitoring:command-results"
    )
    assert control.dead_letter() == (
        "monitor:test:monitoring:command-dead-letter"
    )

    public_runtime = PublicRuntimeRedisKeys(namespace)
    assert public_runtime.current_statuses() == (
        "monitor:test:public:runtime-status"
    )
    assert public_runtime.status_updates() == (
        "monitor:test:public:runtime-status-updates"
    )

    worker_keys = WorkerRedisKeys(namespace)
    assert worker_keys.heartbeat("worker-1") == (
        "monitor:test:workers:worker-1:heartbeat"
    )
    assert worker_keys.active_workers() == (
        "monitor:test:workers:active"
    )
    with pytest.raises(ValueError, match="Invalid worker_id"):
        worker_keys.heartbeat("../unsafe-worker")

    desired_keys = DesiredStateRedisKeys(namespace)
    assert desired_keys.current_states() == (
        "monitor:test:monitoring:desired-streams"
    )
    assert desired_keys.recovery_errors() == (
        "monitor:test:monitoring:desired-state-recovery-errors"
    )


def test_black_screen_key_schemas_stay_inside_check_package():
    keys = BlackScreenRedisKeys(RedisNamespace("monitor:test"))

    assert keys.open_event("stream-1", "v720") == (
        "monitor:test:stream:stream-1:black:variant:v720:open"
    )
    assert keys.event("stream-1", "v720", "event-1") == (
        "monitor:test:stream:stream-1:black:variant:v720:"
        "event:event-1:details"
    )
    assert keys.commit_marker(
        "stream-1",
        "v720",
        3,
        100,
        timeline_generation=2,
        media_revision="revision-1",
    ) == (
        "monitor:test:stream:stream-1:black:variant:v720:timeline:2:"
        "disc:3:segment:100:revision:revision-1:event-committed"
    )
    assert keys.short_history("stream-1", "v720", 2) == (
        "monitor:test:stream:stream-1:black:variant:v720:"
        "timeline:2:short-history"
    )


def test_audio_loss_keys_are_variant_and_timeline_scoped():
    keys = AudioLossRedisKeys(RedisNamespace("monitor:test"))

    assert keys.open_event("stream-1", "v720") == (
        "monitor:test:stream:stream-1:audio-loss:variant:v720:open"
    )
    assert keys.event("stream-1", "v720", "event-1") == (
        "monitor:test:stream:stream-1:audio-loss:variant:v720:"
        "event:event-1:details"
    )
    assert keys.event_lock("stream-1", "v720") == (
        "monitor:test:stream:stream-1:audio-loss:variant:v720:event-lock"
    )
    assert keys.commit_marker(
        "stream-1",
        "v720",
        3,
        100,
        timeline_generation=2,
        media_revision="revision-1",
    ) == (
        "monitor:test:stream:stream-1:audio-loss:variant:v720:timeline:2:"
        "disc:3:segment:100:revision:revision-1:event-committed"
    )


def test_redis_keys_use_storage_id_not_external_stream_id():
    from hashlib import sha256
    from models.stream import build_stream_identity

    stream_identity = build_stream_identity(
        "https://example/master.m3u8",
        "channel-01",
    )
    assert stream_identity.external_stream_id == "channel-01"
    expected_storage_id = sha256("channel-01".encode("utf-8")).hexdigest()[:24]
    assert stream_identity.storage_id == expected_storage_id

    namespace = RedisNamespace("monitor:test")
    processing = ProcessingRedisKeys(namespace)
    runtime = RuntimeRedisKeys(namespace)

    seg_identity = SegmentProcessingIdentity(
        storage_id=stream_identity.storage_id,
        check_name="black_screen",
        variant_stable_id="v720",
        timeline_generation=1,
        discontinuity_sequence=0,
        sequence=10,
        media_revision="rev1",
    )

    seg_key = processing.segment_state(seg_identity)
    assert f"stream:{expected_storage_id}" in seg_key
    assert "stream:channel-01" not in seg_key

    health_key = runtime.health(stream_identity.storage_id)
    assert f"stream:{expected_storage_id}" in health_key
    assert "stream:channel-01" not in health_key
