from datetime import datetime, timezone

from checks.video_freeze.alert_publisher import VideoFreezeAlertPublisher
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from checks.video_freeze.repeated_state import (
    FreezeWarningRecord,
    RepeatedFreezeAlert,
    RepeatedFreezeIncident,
)
from models.freeze import VideoFreezeLiveEvent
from models.segment import Segment
from policies.video_freeze import VideoFreezeAlertPolicy


class RedisRepeatedFreezeRepository:
    """Maintains a bounded warning window under the store's variant lock."""

    def __init__(
        self,
        *,
        storage_id: str,
        redis_client,
        policy: VideoFreezeAlertPolicy,
        freeze_keys: VideoFreezeRedisKeys,
        event_ttl_seconds: int,
        alerts: VideoFreezeAlertPublisher,
    ) -> None:
        self.storage_id = storage_id
        self.redis = redis_client
        self.policy = policy
        self.keys = freeze_keys
        self.event_ttl_seconds = event_ttl_seconds
        self.alerts = alerts

    def queue_records(
        self,
        pipeline,
        events: list[VideoFreezeLiveEvent],
    ) -> None:
        if not events:
            return
        event = events[-1]
        if any(
            item.variant_stable_id != event.variant_stable_id
            or item.timeline_generation != event.timeline_generation
            for item in events
        ):
            raise ValueError(
                "repeated freeze records must share variant and timeline"
            )
        history_key, duration_key, incident_key = self._keys(event)
        timestamps = [
            (item.end_program_time or datetime.now(timezone.utc)).timestamp()
            for item in events
        ]
        minimum = max(timestamps) - self.policy.repeated_window
        records = self._load_records(history_key, duration_key, minimum)
        for item, event_at in zip(events, timestamps):
            records[item.event_id] = FreezeWarningRecord(
                event_id=item.event_id,
                event_at=event_at,
                duration=item.duration,
            )
        ordered = sorted(records.values(), key=lambda item: item.event_at)
        incident = self._load_incident(incident_key)
        new_alert = None

        if incident is None and self.policy.is_repeated_freeze(len(ordered)):
            incident = RepeatedFreezeIncident(
                incident_id=event.event_id,
                first_event_id=ordered[0].event_id,
                latest_event_id=event.event_id,
                first_event_at=ordered[0].event_at,
                last_event_at=max(timestamps),
                occurrences=len(ordered),
                total_duration=sum(item.duration for item in ordered),
            )
            new_alert = RepeatedFreezeAlert(
                state="OPEN",
                event_id=incident.incident_id,
                latest_event_id=incident.latest_event_id,
                occurrences=incident.occurrences,
                total_duration=incident.total_duration,
                reason="repeated_video_freeze",
            )
        elif incident is not None:
            additions = [
                item
                for item in events
                if item.event_id != incident.latest_event_id
            ]
            if not additions:
                additions = []
            incident = RepeatedFreezeIncident(
                incident_id=incident.incident_id,
                first_event_id=incident.first_event_id,
                latest_event_id=(
                    additions[-1].event_id
                    if additions
                    else incident.latest_event_id
                ),
                first_event_at=incident.first_event_at,
                last_event_at=(
                    max(timestamps) if additions else incident.last_event_at
                ),
                occurrences=incident.occurrences + len(additions),
                total_duration=incident.total_duration
                + sum(item.duration for item in additions),
            )

        ttl = max(1, int(self.policy.repeated_window * 2))
        pipeline.delete(history_key, duration_key)
        if ordered:
            pipeline.zadd(
                history_key,
                {item.event_id: item.event_at for item in ordered},
            )
            pipeline.hset(
                duration_key,
                mapping={
                    item.event_id: f"{item.duration:.6f}" for item in ordered
                },
            )
            pipeline.expire(history_key, ttl)
            pipeline.expire(duration_key, ttl)
        if incident is not None:
            pipeline.hset(
                incident_key,
                mapping={
                    "incident_id": incident.incident_id,
                    "first_event_id": incident.first_event_id,
                    "latest_event_id": incident.latest_event_id,
                    "first_event_at": incident.first_event_at,
                    "last_event_at": incident.last_event_at,
                    "occurrences": incident.occurrences,
                    "total_duration": incident.total_duration,
                },
            )
            pipeline.expire(
                incident_key,
                max(self.event_ttl_seconds, ttl),
            )
        if new_alert is not None:
            self.alerts.add_repeated(
                pipeline,
                alert=new_alert,
                variant_id=event.variant_id,
                variant_stable_id=event.variant_stable_id,
            )

    def resolve_if_quiet(self, segment: Segment) -> None:
        history_key, duration_key, incident_key = self._keys(segment)
        incident = self._load_incident(incident_key)
        if incident is None:
            return
        reference = segment.program_date_time or datetime.now(timezone.utc)
        if reference.timestamp() - incident.last_event_at < self.policy.repeated_window:
            return
        pipeline = self.redis.pipeline(transaction=True)
        self.alerts.add_repeated(
            pipeline,
            alert=RepeatedFreezeAlert(
                state="RESOLVED",
                event_id=incident.incident_id,
                latest_event_id=incident.latest_event_id,
                occurrences=incident.occurrences,
                total_duration=incident.total_duration,
                reason="quiet_window_reached",
            ),
            variant_id=segment.variant_id,
            variant_stable_id=segment.variant_stable_id,
        )
        pipeline.delete(history_key, duration_key, incident_key)
        pipeline.execute()

    def _keys(self, source):
        return (
            self.keys.short_history(
                self.storage_id,
                source.variant_stable_id,
                source.timeline_generation,
            ),
            self.keys.short_duration(
                self.storage_id,
                source.variant_stable_id,
                source.timeline_generation,
            ),
            self.keys.repeat_incident(
                self.storage_id,
                source.variant_stable_id,
                source.timeline_generation,
            ),
        )

    def _load_records(self, history_key, duration_key, minimum):
        raw = self.redis.zrangebyscore(
            history_key, minimum, "+inf", withscores=True
        )
        event_ids = [event_id for event_id, _score in raw]
        durations = self.redis.hmget(duration_key, event_ids) if event_ids else []
        return {
            event_id: FreezeWarningRecord(
                event_id=event_id,
                event_at=float(score),
                duration=float(duration or 0),
            )
            for (event_id, score), duration in zip(raw, durations)
        }

    def _load_incident(self, key) -> RepeatedFreezeIncident | None:
        raw = self.redis.hgetall(key)
        if not raw:
            return None
        return RepeatedFreezeIncident(
            incident_id=raw["incident_id"],
            first_event_id=raw["first_event_id"],
            latest_event_id=raw["latest_event_id"],
            first_event_at=float(raw["first_event_at"]),
            last_event_at=float(raw["last_event_at"]),
            occurrences=int(raw["occurrences"]),
            total_duration=float(raw["total_duration"]),
        )
