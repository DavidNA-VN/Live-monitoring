import json
import pytest

from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from core.monitoring_control import MonitoringAction, MonitoringControlResult
from core.redis_keys import ControlRedisKeys

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

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self
        return record

    def execute(self):
        for name, args, kwargs in self.operations:
            getattr(self.redis, name)(*args, **kwargs)
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.streams = {}
        self.acked = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    def xadd(self, key, fields, **_kwargs):
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"

    def xack(self, key, group, entry_id):
        self.acked.append((key, group, entry_id))
        return 1

    def pipeline(self, transaction=True):
        return Pipeline(self)


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


def test_valid_command_publishes_result_and_acknowledges():
    redis = FakeRedis()
    control = Control()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
        handler=MonitoringCommandHandler(control),
        keys=keys,
    )

    consumer._process("1-0", {"payload": command_payload()})

    result = json.loads(redis.streams[keys.command_results()][0]["payload"])
    assert result["status"] == "APPLIED"
    assert result["stream_id"] == "channel-01"
    assert control.starts == 1
    assert redis.acked == [(keys.commands(), "monitoring-workers", "1-0")]


def test_duplicate_command_id_is_not_executed_twice():
    redis = FakeRedis()
    control = Control()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
        handler=MonitoringCommandHandler(control),
        keys=keys,
    )

    consumer._process("1-0", {"payload": command_payload()})
    consumer._process("2-0", {"payload": command_payload()})

    assert control.starts == 1
    assert len(redis.streams[keys.command_results()]) == 1
    assert len(redis.acked) == 2


def test_invalid_command_goes_to_dead_letter_and_is_acknowledged():
    redis = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
        handler=MonitoringCommandHandler(Control()),
        keys=keys,
    )

    consumer._process("3-0", {"payload": "{not-json"})

    assert redis.streams[keys.dead_letter()][0]["error_code"] == "INVALID_COMMAND"
    assert redis.acked == [(keys.commands(), "monitoring-workers", "3-0")]


def test_reused_command_id_with_different_payload_goes_to_dead_letter():
    redis = FakeRedis()
    control = Control()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
        handler=MonitoringCommandHandler(control),
        keys=keys,
    )
    consumer._process("1-0", {"payload": command_payload()})
    changed = json.loads(command_payload())
    changed["config"]["master_url"] = "https://example.test/other.m3u8"

    consumer._process("2-0", {"payload": json.dumps(changed)})

    assert control.starts == 1
    assert redis.streams[keys.dead_letter()][0]["error_code"] == "DUPLICATE_COMMAND_ID"


def test_consumer_readiness_lifecycle():
    from threading import Event, Thread
    import time
    import redis

    class ReadyRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.should_fail = False

        def xgroup_create(self, *args, **kwargs):
            return True

        def xautoclaim(self, *args, **kwargs):
            if self.should_fail:
                raise redis.RedisError("Connection lost")
            return ["0-0", []]

        def xreadgroup(self, *args, **kwargs):
            if self.should_fail:
                raise redis.RedisError("Connection lost")
            return []

    r = ReadyRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=r,
        handler=MonitoringCommandHandler(Control()),
        keys=keys,
        block_milliseconds=10,
        poll_retry_backoff=0.01,
    )

    assert consumer.is_ready is False

    stop_event = Event()
    thread = Thread(target=consumer.run, args=(stop_event,), daemon=True)
    thread.start()

    time.sleep(0.05)
    # Consumer should now be ready after ensure_group succeeds
    assert consumer.is_ready is True

    # Simulate redis error
    r.should_fail = True
    time.sleep(0.05)
    # Should clear readiness on error
    assert consumer.is_ready is False

    # Recover
    r.should_fail = False
    time.sleep(0.05)
    assert consumer.is_ready is True

    # Stop
    stop_event.set()
    thread.join(timeout=1.0)
    assert consumer.is_ready is False


def test_persistence_failure_leaves_command_pending():
    from core.desired_state_repository import DesiredStatePersistenceError

    class FailingPersistenceHandler:
        def handle(self, command):
            raise DesiredStatePersistenceError("Cannot save desired state")

    redis = FakeRedis()
    keys = ControlRedisKeys()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
        handler=FailingPersistenceHandler(),
        keys=keys,
    )

    with pytest.raises(RuntimeError):
        consumer._process("1-0", {"payload": command_payload()})

    # Must NOT acknowledge
    assert redis.acked == []
    # Must NOT set processed marker
    assert not any("processed" in k for k in redis.values)
    # Must NOT post to results stream
    assert keys.command_results() not in redis.streams
    # Must NOT post to dead letter
    assert keys.dead_letter() not in redis.streams


def test_deferred_command_blocks_later_entries_until_persistence_recovers():
    from datetime import datetime, timezone
    from core.desired_state_repository import DesiredStatePersistenceError
    from models.monitoring_command import (
        MonitoringCommandResult,
        MonitoringCommandResultStatus,
    )

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

    redis = FakeRedis()
    handler = FailOnceHandler()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=redis,
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
