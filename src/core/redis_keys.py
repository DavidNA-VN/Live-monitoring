from dataclasses import dataclass

from models.processing import SegmentProcessingIdentity


@dataclass(frozen=True)
class RedisNamespace:
    prefix: str = "media-monitor:v1"

    def __post_init__(self) -> None:
        normalized = self.prefix.strip(":")
        if not normalized:
            raise ValueError("Redis key prefix must not be empty")
        object.__setattr__(self, "prefix", normalized)


class ProcessingRedisKeys:
    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    @property
    def prefix(self) -> str:
        return self.namespace.prefix

    def segment_state(self, identity: SegmentProcessingIdentity) -> str:
        return self._segment_key(identity, "state")

    def segment_lock(self, identity: SegmentProcessingIdentity) -> str:
        return self._segment_key(identity, "lock")

    def timeline_generation(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:"
            f"variant:{variant_stable_id}:timeline-generation"
        )

    def _segment_key(
        self,
        identity: SegmentProcessingIdentity,
        suffix: str,
    ) -> str:
        return (
            f"{self.prefix}:stream:{identity.stream_id}:"
            f"check:{identity.check_name}:"
            f"variant:{identity.variant_stable_id}:"
            f"timeline:{identity.timeline_generation}:"
            f"disc:{identity.discontinuity_sequence}:"
            f"segment:{identity.sequence}:"
            f"revision:{identity.media_revision or 'legacy'}:{suffix}"
        )


class RuntimeRedisKeys:
    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    @property
    def prefix(self) -> str:
        return self.namespace.prefix

    def active_variants(self, stream_id: str) -> str:
        return f"{self.prefix}:stream:{stream_id}:runtime:active-variants"

    def health(self, stream_id: str) -> str:
        return f"{self.prefix}:stream:{stream_id}:runtime:health"

    def metrics(self, stream_id: str) -> str:
        return f"{self.prefix}:stream:{stream_id}:runtime:metrics"


class AlertRedisKeys:
    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    @property
    def prefix(self) -> str:
        return self.namespace.prefix

    def outbox(self) -> str:
        return f"{self.prefix}:alerts:outbox"

    def dead_letter(self) -> str:
        return f"{self.prefix}:alerts:dead-letter"
