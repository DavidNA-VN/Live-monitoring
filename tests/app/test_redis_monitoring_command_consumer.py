from datetime import datetime, timezone
import json
from threading import Event, Thread
import time
import pytest
import redis

from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import (
    RedisMonitoringCommandConsumer,
    _CommandDeferred,
)
from core.desired_state_repository import DesiredStatePersistenceError
from core.monitoring_control import MonitoringAction, MonitoringControlResult
from core.redis_keys import ControlRedisKeys
from models.monitoring_command import (
    MonitoringCommandResult,
    MonitoringCommandResultStatus,
)


class Control:
    def __init__(self):
        self.starts = 0

    def start(self, config):
        self.starts += 1
        return MonitoringControlResult(config.stream_id, MonitoringAction.START, True)


class Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []
        self.should_fail = False

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self
        return record

    def execute(self):
        if self.should_fail or getattr(self.redis, "pipeline_should_fail", False):
            raise redis.RedisError("Pipeline execution failed")
        for name, args, kwargs in self.operations:
            getattr(self.redis, name)(*args, **kwargs)
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.streams = {}
        self.acked = []
        self.should_fail = False
        self.pipeline_should_fail = False
        self.own_pending = []
        self.claimed_pending = []
        self.new_entries = []
        self.pending_count = 0

    def get(self, key):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        return self.values.get(key)

    def set(self, key, value, **_kwargs):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        self.values[key] = value
        return True

    def xadd(self, key, fields, **_kwargs):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"

    def xack(self, key, group, entry_id):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        self.acked.append((key, group, entry_id))
        return 1

    def xgroup_create(self, *args, **kwargs):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        return True

    def xreadgroup(self, group, consumer, streams, count=None, block=None):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        stream_key = list(streams.keys())[0]
        stream_id = streams[stream_key]
        if stream_id == "0":
            if self.own_pending:
                entries = list(self.own_pending)
                self.own_pending.clear()
                return [[stream_key, entries]]
            return []
        elif stream_id == ">":
            if self.new_entries:
                entries = list(self.new_entries)
                self.new_entries.clear()
                return [[stream_key, entries]]
            return []
        return []

    def xautoclaim(self, stream, group, consumer, min_idle_time, start_id, count=None):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        if self.claimed_pending:
            entries = list(self.claimed_pending)
            self.claimed_pending.clear()
            return ["0-0", entries, []]
        return ["0-0", [], []]

    def xpending(self, stream, group):
        if self.should_fail:
            raise redis.RedisError("Redis connection dropped")
        return {"pending": self.pending_count}

    def pipeline(self, transaction=True):
        p = Pipeline(self)
        p.should_fail = self.pipeline_should_fail
        return p


def command_payload(command_id="cmd-1"):
    config = {
        "schema_version": "1.0",
        "stream_id": "channel-01",
        "master_url": "https://example.test/master.m3u8",
        "checks": {
            "black_screen": {"enabled": True},
            "audio_loss": {
                "enabled": True,
                "threshold_dbfs": -60,
                "duration_seconds": 30,
                "track_index": 0,
            },
        },
    }
    return json.dumps({
        "schema_version": "1.0",
        "command_id": command_id,
        "command_type": "START",
        "stream_id": "channel-01",
        "requested_at": "2026-08-26T10:00:00+00:00",
        "config": config,
    })


def test_command_consumer_successful_processing():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    r = FakeRedis()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )
    consumer._process("1-0", {"payload": command_payload()})
    assert ctrl.starts == 1
    assert len(r.acked) == 1
    assert ("media-monitor:v1:monitoring:commands", "monitoring-workers", "1-0") in r.acked


def test_command_consumer_duplicate_id_with_same_payload_is_acked_without_reexecution():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    r = FakeRedis()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )
    payload = command_payload()
    consumer._process("1-0", {"payload": payload})
    assert ctrl.starts == 1

    # Same payload with different stream entry_id
    consumer._process("2-0", {"payload": payload})
    assert ctrl.starts == 1  # Not called again
    assert len(r.acked) == 2


def test_finalize_redis_error_bubbles_without_ack_or_dlq():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    r = FakeRedis()
    r.pipeline_should_fail = True
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )

    with pytest.raises(redis.RedisError):
        consumer._process("1-0", {"payload": command_payload()})

    # Must NOT acknowledge
    assert r.acked == []
    # Must NOT mark processed
    assert not any("processed" in k for k in r.values)
    # Must NOT dead-letter
    assert consumer.keys.dead_letter() not in r.streams


def test_pending_read_order_priority():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    r = FakeRedis()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )

    r.own_pending = [("1-0", {"payload": command_payload("cmd-own")})]
    r.claimed_pending = [("2-0", {"payload": command_payload("cmd-claimed")})]
    r.new_entries = [("3-0", {"payload": command_payload("cmd-new")})]

    # 1. First poll reads own pending only
    processed = consumer.poll_once()
    assert processed == 1
    assert r.acked == [("media-monitor:v1:monitoring:commands", "monitoring-workers", "1-0")]

    # 2. Second poll reads claimed pending only
    processed = consumer.poll_once()
    assert processed == 1
    assert r.acked[-1] == ("media-monitor:v1:monitoring:commands", "monitoring-workers", "2-0")

    # 3. Third poll reads new entries
    processed = consumer.poll_once()
    assert processed == 1
    assert r.acked[-1] == ("media-monitor:v1:monitoring:commands", "monitoring-workers", "3-0")


def test_unclaimable_pending_barrier_blocks_new_commands():
    r = FakeRedis()
    r.pending_count = 1
    r.new_entries = [("2-0", {"payload": command_payload("cmd-new")})]
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=MonitoringCommandHandler(Control()),
    )

    assert consumer.poll_once() == 0
    assert consumer._waiting_for_pending_claim is True
    assert len(r.new_entries) == 1

    r.pending_count = 0
    assert consumer.poll_once() == 1
    assert r.new_entries == []


def test_constructor_rejects_invalid_delivery_settings():
    with pytest.raises(ValueError, match="batch_size"):
        RedisMonitoringCommandConsumer(
            redis_client=FakeRedis(),
            handler=MonitoringCommandHandler(Control()),
            batch_size=0,
        )


def test_stopping_admission_rule():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    r = FakeRedis()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )

    stop_event = Event()
    stop_event.set()

    r.new_entries = [("1-0", {"payload": command_payload("cmd-1")})]
    # When stop_event is set, poll_once does not fetch
    assert consumer.poll_once(stop_event) == 0
    assert ctrl.starts == 0


def test_persistence_failure_leaves_command_pending():
    class FailingPersistenceHandler:
        def handle(self, command):
            raise DesiredStatePersistenceError("Cannot save desired state")

    r = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=FailingPersistenceHandler(),
        keys=keys,
    )

    with pytest.raises(_CommandDeferred):
        consumer._process("1-0", {"payload": command_payload()})

    # Must NOT acknowledge
    assert r.acked == []
    # Must NOT set processed marker
    assert not any("processed" in k for k in r.values)
    # Must NOT post to results stream
    assert keys.command_results() not in r.streams
    # Must NOT post to dead letter
    assert keys.dead_letter() not in r.streams


def test_deferred_command_blocks_later_entries_until_persistence_recovers():
    class FailOnceHandler:
        def __init__(self):
            self.calls = []
            self.failed = False

        def handle(self, command):
            self.calls.append(command.command_id)
            if not self.failed:
                self.failed = True
                raise DesiredStatePersistenceError("temporary failure")
            return MonitoringCommandResult(
                command_id=command.command_id,
                action=command.action,
                stream_id=command.stream_id,
                status=MonitoringCommandResultStatus.APPLIED,
                changed=True,
                processed_at=datetime.now(timezone.utc),
            )

    r = FakeRedis()
    handler = FailOnceHandler()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
    )
    consumer._deferred_entries = [
        ("1-0", {"payload": command_payload("cmd-1")}),
        ("2-0", {"payload": command_payload("cmd-2")}),
    ]

    assert consumer.poll_once() == 0
    assert handler.calls == ["cmd-1"]
    assert len(consumer._deferred_entries) == 2

    assert consumer.poll_once() == 2
    assert handler.calls == ["cmd-1", "cmd-1", "cmd-2"]
    assert consumer._deferred_entries == []
