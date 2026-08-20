from reporting.live_console import LiveAlertConsole
from core.redis_keys import RedisKeyBuilder


class FakePipeline:
    def __init__(self):
        self.dead_letters = []
        self.acks = []

    def xadd(self, key, fields, **options):
        self.dead_letters.append((key, fields, options))
        return self

    def xack(self, stream, group, message_id):
        self.acks.append((stream, group, message_id))
        return self

    def execute(self):
        return []


class FakeRedis:
    def __init__(self, deliveries):
        self.deliveries = deliveries
        self.pipeline_instance = FakePipeline()
        self.direct_acks = []

    def xpending_range(self, *_args, **_kwargs):
        return [{"times_delivered": self.deliveries}]

    def pipeline(self, *, transaction):
        assert transaction is True
        return self.pipeline_instance

    def xack(self, *args):
        self.direct_acks.append(args)


class FakeRedisClient:
    def __init__(self, deliveries):
        self.client = FakeRedis(deliveries)


def failing_console(deliveries):
    client = FakeRedisClient(deliveries)
    console = LiveAlertConsole(
        redis_client=client,
        key_builder=RedisKeyBuilder(prefix="test"),
        max_deliveries=3,
    )
    console._print_alert = lambda _fields: (_ for _ in ()).throw(
        ValueError("bad envelope")
    )
    return console, client.client


def test_poison_message_remains_pending_before_delivery_limit():
    console, redis = failing_console(deliveries=2)

    console._consume_entries("outbox", [("1-0", {"type": "BAD"})])

    assert redis.pipeline_instance.dead_letters == []
    assert redis.direct_acks == []


def test_poison_message_moves_to_bounded_dlq_at_delivery_limit():
    console, redis = failing_console(deliveries=3)

    console._consume_entries("outbox", [("1-0", {"type": "BAD"})])

    key, fields, options = redis.pipeline_instance.dead_letters[0]
    assert key == "test:alerts:dead-letter"
    assert fields["source_message_id"] == "1-0"
    assert fields["delivery_count"] == "3"
    assert options["maxlen"] == 1_000
    assert redis.pipeline_instance.acks == [
        ("outbox", "live-console-v1", "1-0")
    ]
