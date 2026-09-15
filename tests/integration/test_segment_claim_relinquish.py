import os
from uuid import uuid4

import pytest

from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ProcessingRedisKeys, RedisNamespace
from core.segment_state import RedisSegmentStateStore
from models.processing import (
    SegmentClaimStatus,
    SegmentProcessingIdentity,
    SegmentProcessingStatus,
)


pytestmark = pytest.mark.redis_integration


@pytest.fixture
def state_store():
    client = RedisClient(
        RedisSettings(
            url=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"),
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")
    namespace = RedisNamespace(
        f"media-monitor:test:relinquish:{uuid4().hex}"
    )
    store = RedisSegmentStateStore(
        client,
        processing_keys=ProcessingRedisKeys(namespace),
    )
    try:
        yield store
    finally:
        keys = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if keys:
            client.client.delete(*keys)
        client.close()


def _identity(sequence: int) -> SegmentProcessingIdentity:
    return SegmentProcessingIdentity(
        storage_id="storage-1",
        check_name="black_screen",
        variant_stable_id="variant-1",
        timeline_generation=0,
        discontinuity_sequence=0,
        sequence=sequence,
        media_revision=f"revision-{sequence}",
    )


def test_relinquish_returns_claim_without_consuming_attempt(state_store):
    identity = _identity(10)
    first = state_store.claim(identity)
    assert first.status is SegmentClaimStatus.ACQUIRED
    assert first.attempt == 1

    state_store.relinquish(first, "waiting for earlier ordered commit")

    record = state_store.get_records([identity])[identity]
    assert record.status is SegmentProcessingStatus.FAILED_RETRYABLE
    assert record.attempts == 0
    assert record.last_error == "waiting for earlier ordered commit"
    second = state_store.claim(identity)
    assert second.status is SegmentClaimStatus.ACQUIRED
    assert second.attempt == 1
    state_store.mark_success(second)
