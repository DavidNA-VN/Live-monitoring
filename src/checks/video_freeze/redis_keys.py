from core.redis_keys import RedisNamespace


class VideoFreezeRedisKeys:
    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    @property
    def prefix(self) -> str:
        return self.namespace.prefix

    def _variant(self, stream_id: str, variant_stable_id: str) -> str:
        return (
            f"{self.prefix}:freeze:{stream_id}:"
            f"variant:{variant_stable_id}"
        )

    def open_event(self, stream_id: str, variant_stable_id: str) -> str:
        return f"{self._variant(stream_id, variant_stable_id)}:open"

    def event(
        self,
        stream_id: str,
        variant_stable_id: str,
        event_id: str,
    ) -> str:
        return (
            f"{self._variant(stream_id, variant_stable_id)}:"
            f"event:{event_id}:details"
        )

    def event_lock(self, stream_id: str, variant_stable_id: str) -> str:
        return f"{self._variant(stream_id, variant_stable_id)}:lock"

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
            f"{self._variant(stream_id, variant_stable_id)}:"
            f"generation:{timeline_generation}:"
            f"disc:{discontinuity_sequence}:segment:{sequence}:"
            f"revision:{media_revision or 'legacy'}:commit"
        )

    def short_history(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int,
    ) -> str:
        return (
            f"{self._variant(stream_id, variant_stable_id)}:"
            f"generation:{timeline_generation}:short-history"
        )

    def short_duration(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int,
    ) -> str:
        return (
            f"{self._variant(stream_id, variant_stable_id)}:"
            f"generation:{timeline_generation}:short-duration"
        )

    def repeat_incident(
        self,
        stream_id: str,
        variant_stable_id: str,
        timeline_generation: int,
    ) -> str:
        return (
            f"{self._variant(stream_id, variant_stable_id)}:"
            f"generation:{timeline_generation}:repeat-incident"
        )
