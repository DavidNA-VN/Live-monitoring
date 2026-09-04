from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

import redis

from core.redis_client import (
    RedisClient,
    RedisUnavailableError,
)
from core.redis_keys import (
    AlertRedisKeys,
    RuntimeRedisKeys,
)
from core.redis_scripts import PUBLISH_RUNTIME_HEALTH
from models.runtime import (
    LiveCycleStats,
)
from models.alert import ALERT_SCHEMA_VERSION, deterministic_alert_id


def runtime_health_event_id(storage_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"media-monitor:runtime-health:{storage_id}",
        )
    )


class RedisRuntimeHealthReporter:

    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        redis_client: RedisClient,
        runtime_keys: RuntimeRedisKeys | None = None,
        alert_keys: AlertRedisKeys | None = None,
        health_ttl_seconds: int = 120,
        stream_max_length: int = 10_000,
    ):
        if health_ttl_seconds <= 0:
            raise ValueError("health_ttl_seconds must be > 0")
        if stream_max_length <= 0:
            raise ValueError("stream_max_length must be > 0")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.redis = redis_client.client

        self.runtime_keys = runtime_keys or RuntimeRedisKeys()
        self.alert_keys = alert_keys or AlertRedisKeys(
            self.runtime_keys.namespace
        )

        self.health_ttl_seconds = (
            health_ttl_seconds
        )
        self.stream_max_length = stream_max_length

    def publish(
        self,
        stats: LiveCycleStats,
    ) -> None:

        reasons = (
            self._health_reasons(
                stats
            )
        )

        state = (
            "DEGRADED"
            if reasons
            else "HEALTHY"
        )

        payload = json.dumps(
            {
                "state": state,
                "reasons": reasons,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        self._publish(
            state=state,
            reasons=reasons,
            payload=payload,
        )
        self._publish_metrics(stats)

    def publish_failure(
        self,
        reason: str,
    ) -> None:

        payload = json.dumps(
            {
                "state": "DEGRADED",
                "reasons": [
                    reason
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        self._publish(
            state="DEGRADED",
            reasons=[reason],
            payload=payload,
        )

    def _publish_metrics(self, stats: LiveCycleStats) -> None:
        mapping = {
            "started_at": stats.started_at.isoformat(),
            "finished_at": (
                stats.finished_at.isoformat()
                if stats.finished_at is not None
                else ""
            ),
            "variant_count": stats.variant_count,
            "successful_snapshots": stats.successful_snapshots,
            "failed_snapshots": stats.failed_snapshots,
            "scheduled_work": stats.scheduled_work_count,
            "admitted_work": stats.admitted_work_count,
            "backpressure_deferred_work": (
                stats.backpressure_deferred_work_count
            ),
            "queue_depth": stats.queue_depth,
            "queue_lag_seconds": f"{stats.queue_lag_seconds:.6f}",
            "dropped_work": stats.dropped_work_count,
            "dropped_expired_work": (
                stats.dropped_expired_work_count
            ),
            "dropped_capacity_work": (
                stats.dropped_capacity_work_count
            ),
            "playlist_fetch_latency_seconds": (
                f"{stats.playlist_fetch_latency_seconds:.6f}"
            ),
            "playlist_staleness_seconds": (
                f"{stats.playlist_staleness_seconds:.6f}"
            ),
            "analysis_count": stats.analysis_count,
            "analysis_duration_seconds_total": (
                f"{stats.analysis_duration_seconds_total:.6f}"
            ),
            "segment_age_seconds_max": (
                f"{stats.segment_age_seconds_max:.6f}"
            ),
            "retry_total": stats.retry_total,
            "ffmpeg_timeout_total": stats.ffmpeg_timeout_total,
        }
        try:
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.hset(
                self.runtime_keys.metrics(self.storage_id),
                mapping=mapping,
            )
            metrics_key = self.runtime_keys.metrics(self.storage_id)
            counters = {
                "audio_analysis_total": stats.audio_analysis_total,
                "audio_analysis_failure_total": (
                    stats.audio_analysis_failure_total
                ),
                "audio_analysis_timeout_total": (
                    stats.audio_analysis_timeout_total
                ),
                "audio_track_missing_total": stats.audio_track_missing_total,
            }
            for name, value in counters.items():
                if value:
                    pipeline.hincrby(metrics_key, name, value)
            for name, value in stats.profile_metrics.items():
                if not value:
                    continue
                if isinstance(value, int):
                    pipeline.hincrby(metrics_key, name, value)
                else:
                    pipeline.hincrbyfloat(metrics_key, name, value)
            if stats.audio_silence_seconds_total:
                pipeline.hincrbyfloat(
                    metrics_key,
                    "audio_silence_seconds_total",
                    stats.audio_silence_seconds_total,
                )
            pipeline.expire(
                metrics_key,
                self.health_ttl_seconds,
            )
            pipeline.execute()
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Unable to publish runtime metrics: {exc}"
            ) from exc

    def _publish(
        self,
        state: str,
        reasons: list[str],
        payload: str,
    ) -> None:

        event_id = runtime_health_event_id(self.storage_id)
        output_state = "RECOVERED" if state == "HEALTHY" else state
        output_reason = (
            "runtime_recovered"
            if state == "HEALTHY"
            else ",".join(reasons)
        )
        alert_id = deterministic_alert_id(
            stream_id=self.storage_id,
            event_id=event_id,
            state=output_state,
            reason=output_reason,
            revision=payload,
        )
        alert_payload = json.dumps(
            {"reasons": ",".join(reasons)},
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            self.redis.eval(
                PUBLISH_RUNTIME_HEALTH,
                3,
                self.runtime_keys.health(
                    self.storage_id
                ),
                self.alert_keys.outbox(),
                self.runtime_keys.metrics(self.storage_id),
                payload,
                self.health_ttl_seconds,
                state,
                self.external_stream_id,
                ",".join(
                    reasons
                ),
                ALERT_SCHEMA_VERSION,
                alert_id,
                event_id,
                datetime.now(timezone.utc).isoformat(),
                self.stream_max_length,
                alert_payload,
            )

        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to publish runtime "
                    f"health: {exc}"
                )
            ) from exc

    @staticmethod
    def _health_reasons(
        stats: LiveCycleStats,
    ) -> list[str]:

        reasons: list[str] = []

        if stats.failed_snapshots > 0:
            reasons.append(
                (
                    "snapshot_failures="
                    f"{stats.failed_snapshots}"
                )
            )

        if stats.missed_sequence_count > 0:
            reasons.append(
                (
                    "missed_sequences="
                    f"{stats.missed_sequence_count}"
                )
            )

        if stats.timeline_reset_count > 0:
            reasons.append(
                (
                    "timeline_resets="
                    f"{stats.timeline_reset_count}"
                )
            )

        if stats.timeline_conflict_count > 0:
            reasons.append(
                (
                    "timeline_conflicts="
                    f"{stats.timeline_conflict_count}"
                )
            )

        if stats.backpressure_deferred_work_count > 0:
            reasons.append(
                (
                    "backpressure_deferred_work="
                    f"{stats.backpressure_deferred_work_count}"
                )
            )

        if stats.dropped_work_count > 0:
            reasons.append(
                "dropped_work="
                f"{stats.dropped_work_count}"
            )

        if stats.successful_snapshots == 0:
            reasons.append(
                "no_successful_snapshots"
            )

        return reasons
