from models.processing import (
    SegmentProcessingIdentity,
)


class RedisKeyBuilder:

    def __init__(
        self,
        prefix: str = "media-monitor:v1",
    ):
        normalized = prefix.strip(
            ":"
        )

        if not normalized:
            raise ValueError(
                "Redis key prefix must not be empty"
            )

        self.prefix = normalized

    def segment_state(
        self,
        identity: SegmentProcessingIdentity,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{identity.stream_id}:"
            f"check:{identity.check_name}:"
            f"variant:{identity.variant_stable_id}:"
            f"disc:{identity.discontinuity_sequence}:"
            f"segment:{identity.sequence}:"
            "state"
        )

    def segment_lock(
        self,
        identity: SegmentProcessingIdentity,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{identity.stream_id}:"
            f"check:{identity.check_name}:"
            f"variant:{identity.variant_stable_id}:"
            f"disc:{identity.discontinuity_sequence}:"
            f"segment:{identity.sequence}:"
            "lock"
        )
    def black_open_event(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            "open"
        )


    def black_event(
        self,
        stream_id: str,
        event_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"event:{event_id}:"
            "details"
        )


    def black_event_lock(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            "event-lock"
        )


    def black_commit_marker(
        self,
        stream_id: str,
        variant_stable_id: str,
        discontinuity_sequence: int,
        sequence: int,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            f"disc:{discontinuity_sequence}:"
            f"segment:{sequence}:"
            "event-committed"
        )


    def black_short_history(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            "short-history"
        )


    def black_repeat_incident(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            "repeat-incident"
        )


    def black_short_duration(
        self,
        stream_id: str,
        variant_stable_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"variant:{variant_stable_id}:"
            "short-duration"
        )


    def alert_outbox(
        self,
    ) -> str:

        return (
            f"{self.prefix}:alerts:outbox"
        )

    def alert_dead_letter(self) -> str:
        return f"{self.prefix}:alerts:dead-letter"

    def black_event_index(
        self,
        stream_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:event-index"
        )


    def black_cross_variant_evidence(
        self,
        stream_id: str,
        event_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"event:{event_id}:"
            "cross-variant"
        )


    def runtime_active_variants(
        self,
        stream_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "runtime:active-variants"
        )


    def runtime_health(
        self,
        stream_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "runtime:health"
        )

    def runtime_metrics(
        self,
        stream_id: str,
    ) -> str:
        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "runtime:metrics"
        )
    def black_correlation_payload(
        self,
        stream_id: str,
        event_id: str,
    ) -> str:

        return (
            f"{self.prefix}:"
            f"stream:{stream_id}:"
            "black:"
            f"event:{event_id}:"
            "correlation"
        )
