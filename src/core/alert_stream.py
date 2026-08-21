from typing import Protocol

from models.alert import AlertEnvelope
from core.redis_keys import AlertRedisKeys, RuntimeRedisKeys


class AlertSink(Protocol):
    def append(self, pipeline, envelope: AlertEnvelope) -> None:
        ...


class RedisAlertStream:
    def __init__(
        self,
        *,
        alert_keys: AlertRedisKeys,
        runtime_keys: RuntimeRedisKeys,
        max_length: int = 10_000,
        metrics_ttl_seconds: int = 120,
    ) -> None:
        if max_length <= 0:
            raise ValueError("max_length must be > 0")
        self.alert_keys = alert_keys
        self.runtime_keys = runtime_keys
        self.max_length = max_length
        self.metrics_ttl_seconds = metrics_ttl_seconds

    def append(self, pipeline, envelope: AlertEnvelope) -> None:
        pipeline.xadd(
            self.alert_keys.outbox(),
            envelope.to_redis_fields(),
            maxlen=self.max_length,
            approximate=False,
        )
        metrics_key = self.runtime_keys.metrics(envelope.stream_id)
        pipeline.hincrby(metrics_key, "alert_total", 1)
        pipeline.hincrby(
            metrics_key,
            f"alert_{envelope.category.value}_total",
            1,
        )
        pipeline.expire(metrics_key, self.metrics_ttl_seconds)
