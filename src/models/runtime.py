from dataclasses import dataclass
from datetime import datetime


@dataclass
class LiveCycleStats:
    started_at: datetime
    finished_at: datetime | None = None

    variant_count: int = 0
    successful_snapshots: int = 0
    failed_snapshots: int = 0

    declared_gap_count: int = 0
    missed_sequence_count: int = 0
    timeline_reset_count: int = 0
    timeline_conflict_count: int = 0

    scheduled_work_count: int = 0
    admitted_work_count: int = 0
    backpressure_deferred_work_count: int = 0

    queue_depth: int = 0
    queue_lag_seconds: float = 0.0
    dropped_work_count: int = 0
    dropped_expired_work_count: int = 0
    dropped_capacity_work_count: int = 0

    playlist_fetch_latency_seconds: float = 0.0
    playlist_staleness_seconds: float = 0.0
    analysis_count: int = 0
    analysis_duration_seconds_total: float = 0.0
    segment_age_seconds_max: float = 0.0
    retry_total: int = 0
    ffmpeg_timeout_total: int = 0

    redis_unavailable: bool = False

    poll_interval: float = 0.0
