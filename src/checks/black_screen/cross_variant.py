from __future__ import annotations

import json
from datetime import datetime
import redis

from core.redis_client import (
    RedisClient,
    RedisUnavailableError,
)
from core.redis_keys import (
    RedisKeyBuilder,
)
from models.black_live import (
    BlackLiveEvent,
)
from models.evidence import (
    CrossVariantEventEvidence,
)
from checks.black_screen.redis_scripts import WRITE_EVENT_EVIDENCE

class RedisBlackCrossVariantCorrelator:

    def __init__(
        self,
        stream_id: str,
        redis_client: RedisClient,
        key_builder: RedisKeyBuilder | None = None,
        time_tolerance: float = 0.25,
        index_retention_seconds: int = 600,
        evidence_ttl_seconds: int = 86_400,
    ):
        if time_tolerance < 0:
            raise ValueError(
                "time_tolerance must be >= 0"
            )
        if index_retention_seconds <= 0:
            raise ValueError(
                (
                    "index_retention_seconds "
                    "must be > 0"
                )
            )

        if evidence_ttl_seconds <= 0:
            raise ValueError(
                (
                    "evidence_ttl_seconds "
                    "must be > 0"
                )
            )

        self.stream_id = stream_id
        self.redis = redis_client.client

        self.keys = (
            key_builder
            or RedisKeyBuilder()
        )

        self.time_tolerance = (
            time_tolerance
        )

        self.index_retention_seconds = (
            index_retention_seconds
        )

        self.evidence_ttl_seconds = (
            evidence_ttl_seconds
        )

    def observe(
        self,
        event: BlackLiveEvent,
    ) -> None:

        evidence_key = (
            self.keys
            .black_cross_variant_evidence(
                stream_id=self.stream_id,
                event_id=event.event_id,
            )
        )

        if (
            event.start_program_time is None
            or event.end_program_time is None
        ):
            try:
                canonical_key = (
                    self.keys.black_event(
                        stream_id=self.stream_id,
                        event_id=event.event_id,
                    )
                )

                self.redis.eval(
                    WRITE_EVENT_EVIDENCE,
                    2,
                    canonical_key,
                    evidence_key,
                    (
                        self.evidence_ttl_seconds
                        * 1000
                    ),
                    "0",
                    "0",
                    "missing_program_date_time",
                )

            except redis.RedisError as exc:
                raise RedisUnavailableError(
                    str(exc)
                ) from exc

            return

        start_ts = (
            event.start_program_time.timestamp()
        )

        end_ts = (
            event.end_program_time.timestamp()
        )

        index_key = (
            self.keys.black_event_index(
                self.stream_id
            )
        )

        correlation_key = (
            self.keys.black_correlation_payload(
                stream_id=self.stream_id,
                event_id=event.event_id,
            )
        )

        correlation_payload = json.dumps(
            {
                "event_id": event.event_id,
                "variant_id": event.variant_id,
                "variant_stable_id": (
                    event.variant_stable_id
                ),
                "start_program_time": (
                    event.start_program_time.isoformat()
                ),
                "end_program_time": (
                    event.end_program_time.isoformat()
                ),
            },
            separators=(",", ":"),
        )

        active_variants_key = (
            self.keys.runtime_active_variants(
                self.stream_id
            )
        )

        try:
            pipeline = self.redis.pipeline(
                transaction=True
            )

            pipeline.set(
                correlation_key,
                correlation_payload,
                ex=self.evidence_ttl_seconds,
            )
            # score = observed event end time.
            # Open events therefore move forward as they grow.
            pipeline.zadd(
                index_key,
                {
                    event.event_id: end_ts
                },
            )

            pipeline.zremrangebyscore(
                index_key,
                "-inf",
                (
                    start_ts
                    - self.index_retention_seconds
                ),
            )

            pipeline.expire(
                index_key,
                self.evidence_ttl_seconds,
            )

            pipeline.hlen(
                active_variants_key
            )

            results = pipeline.execute()

            analyzed_variant_count = int(
                results[-1] or 0
            )

            candidate_ids = (
                self.redis.zrangebyscore(
                    index_key,
                    (
                        start_ts
                        - self.time_tolerance
                    ),
                    "+inf",
                )
            )

            candidate_ids = [
                candidate_id
                for candidate_id in candidate_ids
                if candidate_id != event.event_id
            ]

            candidate_payloads = (
                self._load_event_payloads(
                    candidate_ids
                )
            )

            overlapping: list[
                tuple[
                    str,
                    str,
                    str,
                ]
            ] = []

            for (
                candidate_id,
                payload,
            ) in candidate_payloads:

                if payload is None:
                    continue

                candidate_variant_stable_id = (
                    payload.get(
                        "variant_stable_id"
                    )
                )

                if (
                    candidate_variant_stable_id
                    == event.variant_stable_id
                ):
                    continue

                candidate_start = (
                    self._parse_time(
                        payload.get(
                            "start_program_time"
                        )
                    )
                )

                candidate_end = (
                    self._parse_time(
                        payload.get(
                            "end_program_time"
                        )
                    )
                )

                if (
                    candidate_start is None
                    or candidate_end is None
                ):
                    continue

                if not self._overlaps(
                    start_a=event.start_program_time,
                    end_a=event.end_program_time,
                    start_b=candidate_start,
                    end_b=candidate_end,
                ):
                    continue

                overlapping.append(
                    (
                        candidate_id,
                        candidate_variant_stable_id,
                        payload.get(
                            "variant_id",
                            candidate_variant_stable_id,
                        ),
                    )
                )

            self._persist_evidence(
                event=event,
                evidence_key=evidence_key,
                analyzed_variant_count=(
                    analyzed_variant_count
                ),
                overlapping=overlapping,
            )

        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to correlate black "
                    f"event {event.event_id}: {exc}"
                )
            ) from exc

    def get(
        self,
        event_id: str,
    ) -> CrossVariantEventEvidence:

        key = (
            self.keys
            .black_cross_variant_evidence(
                stream_id=self.stream_id,
                event_id=event_id,
            )
        )

        try:
            data = self.redis.hgetall(
                key
            )

        except redis.RedisError as exc:
            raise RedisUnavailableError(
                str(exc)
            ) from exc

        if not data:
            return CrossVariantEventEvidence(
                checked=False,
                analyzed_variant_count=0,
                overlapping_variant_ids=[],
                reason="not_available",
            )

        overlapping = sorted(
            value
            for field, value
            in data.items()
            if field.startswith(
                "variant:"
            )
        )

        return CrossVariantEventEvidence(
            checked=(
                data.get("checked")
                == "1"
            ),
            analyzed_variant_count=int(
                data.get(
                    "analyzed_variant_count",
                    "0",
                )
            ),
            overlapping_variant_ids=(
                overlapping
            ),
            reason=(
                data.get("reason")
                or None
            ),
        )

    def _load_event_payloads(
        self,
        event_ids: list[str],
    ) -> list[
        tuple[str, dict | None]
    ]:

        if not event_ids:
            return []

        pipeline = self.redis.pipeline(
            transaction=False
        )

        for event_id in event_ids:

            # Correlation payload used for PDT matching.
            pipeline.get(
                self.keys.black_correlation_payload(
                    stream_id=self.stream_id,
                    event_id=event_id,
                )
            )

            # Canonical event MUST exist.
            pipeline.exists(
                self.keys.black_event(
                    stream_id=self.stream_id,
                    event_id=event_id,
                )
            )

        values = pipeline.execute()

        output = []

        for index, event_id in enumerate(
            event_ids
        ):
            raw = values[index * 2]

            canonical_exists = bool(
                values[index * 2 + 1]
            )

            # Never correlate against an orphan event.
            if (
                not raw
                or not canonical_exists
            ):
                output.append(
                    (
                        event_id,
                        None,
                    )
                )
                continue

            try:
                payload = json.loads(
                    raw
                )
            except json.JSONDecodeError:
                payload = None

            output.append(
                (
                    event_id,
                    payload,
                )
            )

        return output

    def _persist_evidence(
        self,
        event: BlackLiveEvent,
        evidence_key: str,
        analyzed_variant_count: int,
        overlapping: list[
            tuple[str, str, str]
        ],
    ) -> None:

        ttl_ms = (
            self.evidence_ttl_seconds
            * 1000
        )

        canonical_key = (
            self.keys.black_event(
                stream_id=self.stream_id,
                event_id=event.event_id,
            )
        )

        self_args: list[str | int] = [
            ttl_ms,
            "1",
            str(analyzed_variant_count),
            "",
        ]

        for (
            _candidate_event_id,
            candidate_stable_id,
            candidate_variant_id,
        ) in overlapping:
            self_args.extend(
                [
                    (
                        "variant:"
                        f"{candidate_stable_id}"
                    ),
                    candidate_variant_id,
                ]
            )

        self.redis.eval(
            WRITE_EVENT_EVIDENCE,
            2,
            canonical_key,
            evidence_key,
            *self_args,
        )

        for (
            candidate_event_id,
            _candidate_stable_id,
            _candidate_variant_id,
        ) in overlapping:

            candidate_canonical_key = (
                self.keys.black_event(
                    stream_id=self.stream_id,
                    event_id=candidate_event_id,
                )
            )

            candidate_evidence_key = (
                self.keys
                .black_cross_variant_evidence(
                    stream_id=self.stream_id,
                    event_id=candidate_event_id,
                )
            )

            self.redis.eval(
                WRITE_EVENT_EVIDENCE,
                2,
                candidate_canonical_key,
                candidate_evidence_key,
                ttl_ms,
                "1",
                str(analyzed_variant_count),
                "",
                (
                    "variant:"
                    f"{event.variant_stable_id}"
                ),
                event.variant_id,
            )

    def _overlaps(
        self,
        start_a: datetime,
        end_a: datetime,
        start_b: datetime,
        end_b: datetime,
    ) -> bool:

        tolerance = (
            self.time_tolerance
        )

        return (
            start_a.timestamp()
            <= end_b.timestamp() + tolerance
            and start_b.timestamp()
            <= end_a.timestamp() + tolerance
        )

    @staticmethod
    def _parse_time(
        value: str | None,
    ) -> datetime | None:

        if not value:
            return None

        try:
            return datetime.fromisoformat(
                value
            )
        except ValueError:
            return None
