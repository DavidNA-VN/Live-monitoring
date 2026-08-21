from core.redis_keys import RedisNamespace


class BlackScreenRedisKeys:
    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    @property
    def prefix(self) -> str:
        return self.namespace.prefix

    def open_event(self, stream_id: str, variant_stable_id: str) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:black:"
            f"variant:{variant_stable_id}:open"
        )

    def event(self, stream_id: str, event_id: str) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:black:"
            f"event:{event_id}:details"
        )

    def event_lock(self, stream_id: str, variant_stable_id: str) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:black:"
            f"variant:{variant_stable_id}:event-lock"
        )

    def commit_marker(
        self,
        stream_id: str,
        variant_stable_id: str,
        discontinuity_sequence: int,
        sequence: int,
        timeline_generation: int = 0,
        media_revision: str = "",
    ) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:black:"
            f"variant:{variant_stable_id}:"
            f"timeline:{timeline_generation}:"
            f"disc:{discontinuity_sequence}:"
            f"segment:{sequence}:"
            f"revision:{media_revision or 'legacy'}:event-committed"
        )

    def short_history(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int = 0,
    ) -> str:
        return self._timeline_key(
            stream_id, variant_stable_id, timeline_generation, "short-history"
        )

    def repeat_incident(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int = 0,
    ) -> str:
        return self._timeline_key(
            stream_id, variant_stable_id, timeline_generation, "repeat-incident"
        )

    def short_duration(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int = 0,
    ) -> str:
        return self._timeline_key(
            stream_id, variant_stable_id, timeline_generation, "short-duration"
        )

    def _timeline_key(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int,
        suffix: str,
    ) -> str:
        return (
            f"{self.prefix}:stream:{stream_id}:black:"
            f"variant:{variant_stable_id}:"
            f"timeline:{timeline_generation}:{suffix}"
        )
