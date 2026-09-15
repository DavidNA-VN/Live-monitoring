from core.redis_keys import RedisNamespace


class MacroblockingRedisKeys:
    check_name = "macroblocking"

    def __init__(self, namespace: RedisNamespace | None = None) -> None:
        self.namespace = namespace or RedisNamespace()

    def _variant(self, storage_id: str, variant_stable_id: str) -> str:
        return (
            f"{self.namespace.prefix}:stream:{storage_id}:check:{self.check_name}:"
            f"variant:{variant_stable_id}"
        )

    def open_event(self, storage_id: str, variant_stable_id: str) -> str:
        return f"{self._variant(storage_id, variant_stable_id)}:open-event"

    def event(
        self, storage_id: str, variant_stable_id: str, event_id: str
    ) -> str:
        return (
            f"{self._variant(storage_id, variant_stable_id)}:"
            f"event:{event_id}:details"
        )

    def event_lock(self, storage_id: str, variant_stable_id: str) -> str:
        return f"{self._variant(storage_id, variant_stable_id)}:event-lock"

    def alert_recovery(self, storage_id: str, variant_stable_id: str) -> str:
        return f"{self._variant(storage_id, variant_stable_id)}:alert-recovery"

    def commit_marker(
        self,
        storage_id: str,
        variant_stable_id: str,
        discontinuity_sequence: int,
        sequence: int,
        timeline_generation: int = 0,
        media_revision: str = "",
    ) -> str:
        return (
            f"{self._variant(storage_id, variant_stable_id)}:"
            f"timeline:{timeline_generation}:disc:{discontinuity_sequence}:"
            f"segment:{sequence}:revision:{media_revision or 'legacy'}:"
            "event-committed"
        )
