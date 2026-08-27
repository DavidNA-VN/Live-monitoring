from datetime import datetime, timezone
import json
from threading import Event, Thread
import time
import pytest
import redis

from app.command_guardrails import CommandGuardrails
from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import (
    RedisMonitoringCommandConsumer,
    _CommandDeferred,
)
from core.command_metrics import CommandMetricsCollector
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


def command_payload(command_id="cmd-1", requested_at=None):
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
        "requested_at": requested_at or "2026-08-26T10:00:00+00:00",
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
        ("1-0", {"payload": command_payload("cmd-1")}, False),
        ("2-0", {"payload": command_payload("cmd-2")}, False),
    ]

    assert consumer.poll_once() == 0
    assert handler.calls == ["cmd-1"]
    assert len(consumer._deferred_entries) == 2

    assert consumer.poll_once() == 2
    assert handler.calls == ["cmd-1", "cmd-1", "cmd-2"]
    assert consumer._deferred_entries == []


def test_reclaimed_command_keeps_age_exemption_when_deferred():
    class FailOnceHandler:
        def __init__(self):
            self.calls = 0

        def handle(self, command):
            self.calls += 1
            if self.calls == 1:
                raise DesiredStatePersistenceError("temporary failure")
            return MonitoringCommandResult(
                command_id=command.command_id,
                action=command.action,
                stream_id=command.stream_id,
                status=MonitoringCommandResultStatus.APPLIED,
                changed=True,
                processed_at=datetime.now(timezone.utc),
            )

    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    redis_client = FakeRedis()
    redis_client.claimed_pending = [
        (
            "1-0",
            {
                "payload": command_payload(
                    "cmd-reclaimed",
                    requested_at="2020-01-01T00:00:00Z",
                )
            },
        )
    ]
    handler = FailOnceHandler()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis_client,
        handler=handler,
        guardrails=CommandGuardrails(max_command_age_seconds=60.0),
        utc_now=lambda: now,
    )

    assert consumer.poll_once() == 0
    assert consumer._deferred_entries[0][2] is True
    assert consumer.poll_once() == 1
    assert handler.calls == 2
    assert consumer._deferred_entries == []


def test_oversized_payload_safe_dead_letter_and_metrics():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    collector = CommandMetricsCollector()
    guardrails = CommandGuardrails(max_payload_bytes=50)

    r = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
        keys=keys,
        guardrails=guardrails,
        metrics=collector,
    )

    oversized_text = "x" * 100
    consumer._process("1-0", {"payload": oversized_text})

    # Assert ACKed
    assert r.acked == [(keys.commands(), "monitoring-workers", "1-0")]
    # Assert DLQ entry created
    dlq = r.streams.get(keys.dead_letter(), [])
    assert len(dlq) == 1
    dlq_entry = dlq[0]
    assert dlq_entry["source_entry_id"] == "1-0"
    assert dlq_entry["payload_size_bytes"] == "100"
    assert dlq_entry["error_code"] == "COMMAND_PAYLOAD_TOO_LARGE"
    # Raw payload must NOT be saved in DLQ
    assert "payload" not in dlq_entry

    # Assert metrics updated
    snap = collector.snapshot("worker-1")
    assert snap.command_oversized_total == 1
    assert snap.command_dead_letter_total == 1
    assert snap.last_error_code == "COMMAND_PAYLOAD_TOO_LARGE"


def test_stale_command_rejection_and_metrics():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    collector = CommandMetricsCollector()
    guardrails = CommandGuardrails(max_command_age_seconds=60.0)

    r = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
        keys=keys,
        guardrails=guardrails,
        metrics=collector,
    )

    old_requested = "2020-01-01T00:00:00Z"
    stale_payload = command_payload("cmd-stale", requested_at=old_requested)

    consumer._process("1-0", {"payload": stale_payload})

    # Assert ACKed
    assert r.acked == [(keys.commands(), "monitoring-workers", "1-0")]
    # Assert result stream has REJECTED status with STALE_COMMAND error_code
    results = r.streams.get(keys.command_results(), [])
    assert len(results) == 1
    res_data = json.loads(results[0]["payload"])
    assert res_data["status"] == "REJECTED"
    assert res_data["error_code"] == "STALE_COMMAND"

    # Assert metrics updated
    snap = collector.snapshot("worker-1")
    assert snap.command_stale_total == 1
    assert snap.command_rejected_total == 1


def test_duplicate_replay_increments_replay_metric():
    ctrl = Control()
    handler = MonitoringCommandHandler(ctrl)
    collector = CommandMetricsCollector()

    r = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=handler,
        keys=keys,
        metrics=collector,
    )

    payload = command_payload("cmd-replay")
    # First execution -> APPLIED
    consumer._process("1-0", {"payload": payload})
    snap1 = collector.snapshot("worker-1")
    assert snap1.command_applied_total == 1
    assert snap1.command_duplicate_replay_total == 0

    # Second execution with same payload -> Replay ACK
    consumer._process("2-0", {"payload": payload})
    snap2 = collector.snapshot("worker-1")
    assert snap2.command_applied_total == 1
    assert snap2.command_duplicate_replay_total == 1
