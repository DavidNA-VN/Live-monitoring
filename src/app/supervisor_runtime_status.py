from __future__ import annotations

from datetime import datetime
import json
import logging
import math
from typing import Any

import redis

from core.redis_keys import RuntimeRedisKeys
from core.runtime_status_reader import RuntimeStatusReader
from core.stream_session import StreamSessionStatus
from core.stream_supervisor import StreamSupervisor
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)

logger = logging.getLogger(__name__)


_LIFECYCLE_STATUS_MAP: dict[StreamSessionStatus, PublicStreamStatus] = {
    StreamSessionStatus.CREATED: PublicStreamStatus.CREATED,
    StreamSessionStatus.RUNNING: PublicStreamStatus.RUNNING,
    StreamSessionStatus.PAUSED: PublicStreamStatus.PAUSED,
    StreamSessionStatus.STOPPING: PublicStreamStatus.STOPPING,
    StreamSessionStatus.STOPPED: PublicStreamStatus.STOPPED,
    StreamSessionStatus.FAILED: PublicStreamStatus.FAILED,
}


class SupervisorRuntimeStatusReader:
    """Reads complete runtime monitoring status combining supervisor lifecycle and Redis telemetry."""

    def __init__(
        self,
        *,
        supervisor: StreamSupervisor,
        redis_client: Any,
        runtime_keys: RuntimeRedisKeys | None = None,
        custom_logger: logging.Logger | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.redis_client = redis_client
        self.runtime_keys = runtime_keys or RuntimeRedisKeys()
        self.logger = custom_logger or logger

    def get(self, stream_id: str) -> RuntimeStatus | None:
        snap = self.supervisor.snapshot(stream_id)
        if snap is None:
            return None

        config = self.supervisor.configuration(stream_id)
        if config is None:
            return None

        storage_id = config.identity.storage_id
        public_status = _LIFECYCLE_STATUS_MAP[snap.status]

        checks = {
            "black_screen": (
                CheckStatus.ENABLED
                if config.black_screen_enabled
                else CheckStatus.DISABLED
            ),
            "video_freeze": (
                CheckStatus.ENABLED
                if config.video_freeze_enabled
                else CheckStatus.DISABLED
            ),
            "audio_loss": (
                CheckStatus.ENABLED
                if config.audio_loss_enabled
                else CheckStatus.DISABLED
            ),
        }

        # Non-RUNNING sessions: lifecycle is source of truth, disregard stale Redis telemetry
        if snap.status != StreamSessionStatus.RUNNING:
            health = (
                RuntimeHealth.UNHEALTHY
                if snap.status == StreamSessionStatus.FAILED
                else RuntimeHealth.UNKNOWN
            )
            return RuntimeStatus(
                stream_id=stream_id,
                status=public_status,
                health=health,
                active_variant_count=0,
                queue_depth=0,
                checks=checks,
                started_at=snap.started_at,
                last_poll_at=None,
                queue_lag_seconds=None,
                live_edge_lag_seconds=None,
                error=snap.error,
                telemetry_available=False,
                health_reasons=(),
            )

        # RUNNING sessions: read Redis telemetry in single pipelined batch
        health_key = self.runtime_keys.health(storage_id)
        metrics_key = self.runtime_keys.metrics(storage_id)
        variants_key = self.runtime_keys.active_variants(storage_id)

        client = getattr(self.redis_client, "client", self.redis_client)
        raw_health = None
        raw_metrics = None
        raw_variant_count = None
        telemetry_available = True

        try:
            pipe = client.pipeline(transaction=False)
            pipe.get(health_key)
            pipe.hgetall(metrics_key)
            pipe.hlen(variants_key)
            results = pipe.execute()
            if not isinstance(results, (list, tuple)) or len(results) != 3:
                raise RuntimeError("Unexpected Redis telemetry pipeline result")
            raw_health = results[0]
            raw_metrics = results[1]
            raw_variant_count = results[2]
        except (redis.RedisError, ConnectionError, TimeoutError, RuntimeError) as exc:
            self.logger.warning(
                "Redis unavailable while reading telemetry for stream_id=%s: %s",
                stream_id,
                exc,
            )
            telemetry_available = False

        health = RuntimeHealth.UNKNOWN
        health_reasons: tuple[str, ...] = ()
        active_variant_count = 0
        queue_depth = 0
        queue_lag_seconds = None
        live_edge_lag_seconds = None
        last_poll_at = None
        admission_mode = "coverage"
        startup_segments_outside_scope = 0
        dropped_expired_work = 0
        dropped_capacity_work = 0
        dropped_live_edge_work = 0
        coverage_gap_count = 0
        coverage_gap_segment_count = 0
        dropped_media_segment_count = 0
        profile_analysis_total: dict[str, int] = {}
        active_media_processes = 0
        max_media_processes = 0

        if telemetry_available:
            # Parse health
            if raw_health:
                try:
                    if isinstance(raw_health, bytes):
                        raw_health = raw_health.decode("utf-8")
                    if isinstance(raw_health, str):
                        health_obj = json.loads(raw_health)
                        if isinstance(health_obj, dict):
                            state_val = health_obj.get("state")
                            if state_val == "HEALTHY":
                                health = RuntimeHealth.HEALTHY
                            elif state_val == "DEGRADED":
                                health = RuntimeHealth.DEGRADED
                            reasons_val = health_obj.get("reasons")
                            if isinstance(reasons_val, list):
                                health_reasons = tuple(str(r) for r in reasons_val)
                except Exception:
                    health = RuntimeHealth.UNKNOWN
                    health_reasons = ()

            # Parse active variants count
            if isinstance(raw_variant_count, int) and raw_variant_count >= 0:
                active_variant_count = raw_variant_count

            # Parse metrics
            if isinstance(raw_metrics, dict):
                def _get_metric_field(field_name: str) -> Any:
                    if field_name in raw_metrics:
                        return raw_metrics[field_name]
                    return raw_metrics.get(field_name.encode("utf-8"))

                qd = _get_metric_field("queue_depth")
                if qd is not None:
                    try:
                        qd_int = int(qd)
                        if qd_int >= 0:
                            queue_depth = qd_int
                    except (ValueError, TypeError):
                        pass

                def _non_negative_int(field_name: str) -> int:
                    raw = _get_metric_field(field_name)
                    try:
                        value = int(raw)
                    except (ValueError, TypeError):
                        return 0
                    return value if value >= 0 else 0

                raw_mode = _get_metric_field("admission_mode")
                if isinstance(raw_mode, bytes):
                    raw_mode = raw_mode.decode("utf-8", errors="replace")
                if raw_mode in {
                    "coverage",
                    "catch_up",
                    "live_edge_protection",
                }:
                    admission_mode = raw_mode

                startup_segments_outside_scope = _non_negative_int(
                    "startup_segments_outside_scope_total"
                )
                dropped_expired_work = _non_negative_int(
                    "dropped_expired_work_total"
                )
                dropped_capacity_work = _non_negative_int(
                    "dropped_capacity_work_total"
                )
                dropped_live_edge_work = _non_negative_int(
                    "dropped_live_edge_work_total"
                )
                coverage_gap_count = _non_negative_int("coverage_gap_total")
                coverage_gap_segment_count = _non_negative_int(
                    "coverage_gap_segment_total"
                )
                dropped_media_segment_count = _non_negative_int(
                    "dropped_media_segments_total"
                )
                active_media_processes = _non_negative_int(
                    "active_media_processes"
                )
                max_media_processes = _non_negative_int(
                    "max_media_processes"
                )
                active_media_processes = min(
                    active_media_processes,
                    max_media_processes,
                )
                for profile, metric_name in {
                    "video_realtime": "video_analysis_total",
                    "audio_realtime": "audio_analysis_total",
                }.items():
                    value = _non_negative_int(metric_name)
                    if value:
                        profile_analysis_total[profile] = value

                ql = _get_metric_field("queue_lag_seconds")
                if ql is not None:
                    try:
                        ql_float = float(ql)
                        if math.isfinite(ql_float) and ql_float >= 0:
                            queue_lag_seconds = ql_float
                    except (ValueError, TypeError):
                        pass

                live_lag = _get_metric_field("live_edge_lag_seconds")
                if live_lag is not None:
                    try:
                        live_lag_float = float(live_lag)
                        if (
                            math.isfinite(live_lag_float)
                            and live_lag_float >= 0
                        ):
                            live_edge_lag_seconds = live_lag_float
                    except (ValueError, TypeError):
                        pass

                fin = _get_metric_field("finished_at")
                if fin is not None:
                    if isinstance(fin, bytes):
                        fin = fin.decode("utf-8")
                    if isinstance(fin, str) and fin:
                        try:
                            last_poll_at = datetime.fromisoformat(fin)
                        except (ValueError, TypeError):
                            last_poll_at = None

        return RuntimeStatus(
            stream_id=stream_id,
            status=public_status,
            health=health,
            active_variant_count=active_variant_count,
            queue_depth=queue_depth,
            checks=checks,
            started_at=snap.started_at,
            last_poll_at=last_poll_at,
            queue_lag_seconds=queue_lag_seconds,
            live_edge_lag_seconds=live_edge_lag_seconds,
            error=snap.error,
            telemetry_available=telemetry_available,
            health_reasons=health_reasons,
            admission_mode=admission_mode,
            startup_segments_outside_scope=startup_segments_outside_scope,
            dropped_expired_work=dropped_expired_work,
            dropped_capacity_work=dropped_capacity_work,
            dropped_live_edge_work=dropped_live_edge_work,
            coverage_gap_count=coverage_gap_count,
            coverage_gap_segment_count=coverage_gap_segment_count,
            dropped_media_segment_count=dropped_media_segment_count,
            profile_analysis_total=profile_analysis_total,
            active_media_processes=active_media_processes,
            max_media_processes=max_media_processes,
        )
