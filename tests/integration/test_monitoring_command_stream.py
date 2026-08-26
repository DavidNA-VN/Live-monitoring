import json
import os
from uuid import uuid4

import pytest

from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from core.monitoring_control import MonitoringAction, MonitoringControlResult
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, RedisNamespace


pytestmark = pytest.mark.redis_integration


class StartControl:
    def __init__(self):
        self.calls = 0

    def start(self, config):
        self.calls += 1
        return MonitoringControlResult(config.stream_id, MonitoringAction.START, True)


@pytest.fixture
def redis_context():
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
    namespace = RedisNamespace(f"media-monitor:test:{uuid4().hex}")
    try:
        yield client, namespace
    finally:
        found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            client.client.delete(*found)
        client.close()


def test_redis_command_is_applied_resulted_and_acked(redis_context):
    client, namespace = redis_context
    keys = ControlRedisKeys(namespace)
    control = StartControl()
    consumer = RedisMonitoringCommandConsumer(
        redis_client=client,
        handler=MonitoringCommandHandler(control),
        keys=keys,
        consumer_name="integration-worker",
        block_milliseconds=10,
        claim_idle_milliseconds=10,
    )
    consumer.ensure_group()
    payload = json.dumps({
        "schema_version": "1.0",
        "command_id": "cmd-integration-1",
        "command_type": "START",
        "stream_id": "channel-01",
        "requested_at": "2026-08-26T10:00:00+00:00",
        "config": {
            "schema_version": "1.0",
            "stream_id": "channel-01",
            "master_url": "https://example.test/master.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -60,
                    "duration_seconds": 30
                }
            }
        }
    })
    client.client.xadd(keys.commands(), {"payload": payload})

    assert consumer.poll_once() == 1
    assert control.calls == 1
    _, fields = client.client.xrevrange(keys.command_results(), count=1)[0]
    result = json.loads(fields["payload"])
    assert result["status"] == "APPLIED"
    assert result["stream_id"] == "channel-01"
    pending = client.client.xpending(keys.commands(), consumer.group_name)
    assert pending["pending"] == 0
