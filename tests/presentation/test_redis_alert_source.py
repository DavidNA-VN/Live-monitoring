import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple
import pytest
import redis.exceptions

from core.redis_keys import AlertRedisKeys, RedisNamespace
from presentation.api.adapters.errors import PresentationServiceUnavailableError
from presentation.api.adapters.redis_alert_source import (
    AlertRedisUnavailableError,
    RedisAlertSource,
)
from presentation.api.models import AlertState, EventType


class FakeAsyncRedisStream:
    """Mock Redis client hỗ trợ XREVRANGE và XREAD phục vụ test RedisAlertSource."""

    def __init__(self) -> None:
        self.streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self.xrevrange_calls: List[Tuple[str, str, str, int]] = []
        self.xread_calls: List[Tuple[Dict[str, str], int, int]] = []
        self.raise_exc: Optional[Exception] = None

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: Optional[int] = None,
    ) -> List[Tuple[bytes, Dict[bytes, bytes]]]:
        self.xrevrange_calls.append((name, max, min, count or 0))
        if self.raise_exc:
            raise self.raise_exc

        all_entries = self.streams.get(name, [])
        # all_entries stored in chronological order (oldest to newest)
        # Reverse to newest-first
        rev_entries = list(reversed(all_entries))

        # Handle max cursor
        start_idx = 0
        if max != "+":
            if max.startswith("("):
                # Exclusive cursor: start after this entry
                exclusive_id = max[1:]
                for i, (eid, _) in enumerate(rev_entries):
                    if eid == exclusive_id:
                        start_idx = i + 1
                        break
            else:
                for i, (eid, _) in enumerate(rev_entries):
                    if eid == max:
                        start_idx = i
                        break

        sliced = rev_entries[start_idx:]
        if count is not None:
            sliced = sliced[:count]

        # Convert to bytes
        res = []
        for eid, fields in sliced:
            b_fields = {k.encode("utf-8"): v.encode("utf-8") for k, v in fields.items()}
            res.append((eid.encode("utf-8"), b_fields))
        return res

    async def xread(
        self,
        streams: Dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None,
    ) -> List[Tuple[bytes, List[Tuple[bytes, Dict[bytes, bytes]]]]]:
        self.xread_calls.append((streams, count or 0, block or 0))
        await asyncio.sleep(0.001)
        if self.raise_exc:
            raise self.raise_exc

        res = []
        for stream_name, cursor in streams.items():
            all_entries = self.streams.get(stream_name, [])
            if cursor == "$":
                # Special cursor: only entries added after $ (simulate empty for initial read)
                continue
            else:
                # Entries with id > cursor
                found = []
                for eid, fields in all_entries:
                    if eid > cursor:
                        b_fields = {k.encode("utf-8"): v.encode("utf-8") for k, v in fields.items()}
                        found.append((eid.encode("utf-8"), b_fields))
                if count:
                    found = found[:count]
                if found:
                    res.append((stream_name.encode("utf-8"), found))
        return res


def make_alert_fields(
    stream_id: str = "chan-01",
    alert_id: str = "alert-01",
    event_id: str = "evt-01",
    event_type: str = "BLACK_SCREEN",
    state: str = "OPEN",
    minute: int = 0,
) -> Dict[str, str]:
    return {
        "schema_version": "1.0",
        "alert_id": alert_id,
        "event_id": event_id,
        "category": "content",
        "type": event_type,
        "state": state,
        "stream_id": stream_id,
        "occurred_at": f"2026-08-28T10:{minute:02d}:00+00:00",
        "emitted_at": f"2026-08-28T10:{minute:02d}:01+00:00",
        "reason": "Threshold reached",
        "payload": json.dumps({"severity": "ALERT"}),
        "severity": "ALERT",
    }


@pytest.mark.anyio
async def test_recent_empty_outbox():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys)

    recent = await source.recent("chan-01", limit=10)
    assert recent == []


@pytest.mark.anyio
async def test_recent_single_stream_chronological():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys)

    # 3 entries in chronological order in stream
    e1 = ("1000-0", make_alert_fields("chan-01", "a-1", "evt-1", minute=1))
    e2 = ("1000-1", make_alert_fields("chan-01", "a-2", "evt-2", minute=2))
    e3 = ("1000-2", make_alert_fields("chan-01", "a-3", "evt-3", minute=3))
    fake_redis.streams[keys.outbox()] = [e1, e2, e3]

    recent = await source.recent("chan-01", limit=10)
    assert len(recent) == 3
    # Chronological: oldest to newest
    assert recent[0].alert_id == "a-1"
    assert recent[1].alert_id == "a-2"
    assert recent[2].alert_id == "a-3"


@pytest.mark.anyio
async def test_recent_preserves_freeze_open_update_resolved_lifecycle():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys)
    lifecycle = []
    for index, state in enumerate(("OPEN", "UPDATE", "RESOLVED"), start=1):
        fields = make_alert_fields(
            "chan-01",
            f"freeze-{state.lower()}",
            "freeze-event-1",
            event_type="VIDEO_FREEZE",
            state=state,
            minute=index,
        )
        fields["reason"] = (
            "video_returned" if state == "RESOLVED" else "freeze_threshold"
        )
        lifecycle.append((f"{index}-0", fields))
    fake_redis.streams[keys.outbox()] = lifecycle

    recent = await source.recent("chan-01", limit=10)

    assert [item.state.value for item in recent] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
    ]
    assert {item.event_id for item in recent} == {"freeze-event-1"}
    assert all(item.event_type is EventType.VIDEO_FREEZE for item in recent)


@pytest.mark.anyio
async def test_recent_multi_stream_filtering_and_limit():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys, history_page_size=2)

    # Mixed streams
    fake_redis.streams[keys.outbox()] = [
        ("1-0", make_alert_fields("chan-A", "a-1", "evt-1", minute=1)),
        ("2-0", make_alert_fields("chan-B", "b-1", "evt-2", minute=2)),
        ("3-0", make_alert_fields("chan-A", "a-2", "evt-3", minute=3)),
        ("4-0", make_alert_fields("chan-A", "a-3", "evt-4", minute=4)),
        ("5-0", make_alert_fields("chan-B", "b-2", "evt-5", minute=5)),
    ]

    # Query chan-A with limit 2
    recent = await source.recent("chan-A", limit=2)
    assert len(recent) == 2
    # The 2 most recent for chan-A are a-2 and a-3, returned chronologically (a-2 then a-3)
    assert recent[0].alert_id == "a-2"
    assert recent[1].alert_id == "a-3"


@pytest.mark.anyio
async def test_recent_skips_poison_entry():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys)

    fake_redis.streams[keys.outbox()] = [
        ("1-0", make_alert_fields("chan-01", "a-1", minute=1)),
        ("2-0", {"corrupt": "invalid-entry"}),  # Poison entry missing required fields
        ("3-0", make_alert_fields("chan-01", "a-2", minute=3)),
    ]

    recent = await source.recent("chan-01", limit=10)
    assert len(recent) == 2
    assert recent[0].alert_id == "a-1"
    assert recent[1].alert_id == "a-2"


@pytest.mark.anyio
async def test_recent_redis_error():
    fake_redis = FakeAsyncRedisStream()
    fake_redis.raise_exc = redis.exceptions.ConnectionError("Redis down")
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(redis_client=fake_redis, keys=keys)

    with pytest.raises(PresentationServiceUnavailableError):
        await source.recent("chan-01", limit=10)


@pytest.mark.anyio
async def test_subscribe_stream_filtering_and_poison_skip():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))
    source = RedisAlertSource(
        redis_client=fake_redis,
        keys=keys,
        xread_block_milliseconds=10,
    )

    # Pre-populate stream
    fake_redis.streams[keys.outbox()] = [
        ("1-0", make_alert_fields("chan-A", "a-1", minute=1)),
        ("2-0", make_alert_fields("chan-B", "b-1", minute=2)),
        ("3-0", {"bad": "poison"}),
        ("4-0", make_alert_fields("chan-A", "a-2", minute=4)),
    ]

    received = []

    async def run_subscriber():
        # Start reading from cursor "0-0"
        async for alert in source.subscribe("chan-A"):
            received.append(alert)
            if len(received) >= 2:
                break

    # We need to simulate that after subscription starts, cursor moves
    # Set initial cursor inside subscribe by replacing subscribe cursor initialization or adding entries
    # In fake_redis, if cursor is "$", it returns empty; if cursor is "0-0", it returns all
    task = asyncio.create_task(run_subscriber())
    await asyncio.sleep(0.01)

    # Add new entries with IDs > 0
    fake_redis.streams[keys.outbox()].append(
        ("5-0", make_alert_fields("chan-A", "a-3", minute=5))
    )
    fake_redis.streams[keys.outbox()].append(
        ("6-0", make_alert_fields("chan-A", "a-4", minute=6))
    )

    # Let the loop execute with next tick
    await asyncio.sleep(0.05)
    await source.close()
    await task

    assert len(received) == 2
    assert received[0].alert_id == "a-3"
    assert received[1].alert_id == "a-4"
    assert fake_redis.xread_calls[0][0][keys.outbox()] == "4-0"


@pytest.mark.anyio
async def test_subscribe_reconnect_backoff_and_reset():
    fake_redis = FakeAsyncRedisStream()
    keys = AlertRedisKeys(RedisNamespace("test-monitor"))

    sleep_calls = []

    async def fake_sleep(d: float):
        sleep_calls.append(d)
        await asyncio.sleep(0)

    source = RedisAlertSource(
        redis_client=fake_redis,
        keys=keys,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=0.4,
        sleep_fn=fake_sleep,
        xread_block_milliseconds=5,
    )

    # Inject connection error
    fake_redis.raise_exc = redis.exceptions.ConnectionError("Redis drop")

    async def run_sub():
        async for _ in source.subscribe("chan-01"):
            pass

    task = asyncio.create_task(run_sub())
    # Let it fail 3 times and backoff
    for _ in range(5):
        await asyncio.sleep(0.001)

    await source.close()
    await asyncio.gather(task, return_exceptions=True)

    assert len(sleep_calls) >= 2
    assert sleep_calls[0] == 0.1
    assert sleep_calls[1] == 0.2
    # Capped at max 0.4
    for s in sleep_calls[2:]:
        assert s <= 0.4
    assert task.cancelled()
