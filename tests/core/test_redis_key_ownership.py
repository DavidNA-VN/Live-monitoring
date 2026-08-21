from checks.black_screen.redis_keys import BlackScreenRedisKeys
from core.redis_keys import (
    AlertRedisKeys,
    ProcessingRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
)
from models.processing import SegmentProcessingIdentity


def test_key_spaces_share_prefix_but_keep_domain_ownership():
    namespace = RedisNamespace(":monitor:test:")
    processing = ProcessingRedisKeys(namespace)
    runtime = RuntimeRedisKeys(namespace)
    alerts = AlertRedisKeys(namespace)
    black = BlackScreenRedisKeys(namespace)

    assert processing.namespace is namespace
    assert runtime.namespace is namespace
    assert alerts.namespace is namespace
    assert black.namespace is namespace
    assert not hasattr(processing, "open_event")
    assert not hasattr(runtime, "segment_state")
    assert not hasattr(alerts, "health")
    assert not hasattr(black, "outbox")


def test_core_key_schemas_are_stable():
    namespace = RedisNamespace("monitor:test")
    processing = ProcessingRedisKeys(namespace)
    runtime = RuntimeRedisKeys(namespace)
    alerts = AlertRedisKeys(namespace)
    identity = SegmentProcessingIdentity(
        stream_id="stream-1",
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


def test_black_screen_key_schemas_stay_inside_check_package():
    keys = BlackScreenRedisKeys(RedisNamespace("monitor:test"))

    assert keys.open_event("stream-1", "v720") == (
        "monitor:test:stream:stream-1:black:variant:v720:open"
    )
    assert keys.event("stream-1", "event-1") == (
        "monitor:test:stream:stream-1:black:event:event-1:details"
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
