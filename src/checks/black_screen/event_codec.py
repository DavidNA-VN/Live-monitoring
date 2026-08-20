import json
from datetime import datetime

from models.black_live import BlackEventStatus, BlackLiveEvent
from models.evidence import EventEvidence, EvidenceStrength


class BlackEventCodec:
    @staticmethod
    def encode(event: BlackLiveEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "variant_id": event.variant_id,
                "variant_stable_id": event.variant_stable_id,
                "discontinuity_sequence": (
                    event.discontinuity_sequence
                ),
                "start_sequence": event.start_sequence,
                "end_sequence": event.end_sequence,
                "start_offset": event.start_offset,
                "end_offset": event.end_offset,
                "start_program_time": (
                    event.start_program_time.isoformat()
                    if event.start_program_time
                    else None
                ),
                "end_program_time": (
                    event.end_program_time.isoformat()
                    if event.end_program_time
                    else None
                ),
                "duration": event.duration,
                "last_segment_duration": (
                    event.last_segment_duration
                ),
                "affected_segments": event.affected_segments,
                "status": event.status.value,
                "long_alert_sent": event.long_alert_sent,
                "resolution_reason": event.resolution_reason,
                "evidence": [
                    {
                        "evidence_type": item.evidence_type,
                        "strength": item.strength.value,
                        "source": item.source,
                        "detail": item.detail,
                    }
                    for item in event.evidence
                ],
            },
            separators=(",", ":"),
        )

    @staticmethod
    def decode(raw: str) -> BlackLiveEvent:
        data = json.loads(raw)
        return BlackLiveEvent(
            event_id=data["event_id"],
            stream_id=data["stream_id"],
            variant_id=data["variant_id"],
            variant_stable_id=data["variant_stable_id"],
            discontinuity_sequence=int(
                data["discontinuity_sequence"]
            ),
            start_sequence=int(data["start_sequence"]),
            end_sequence=int(data["end_sequence"]),
            start_offset=float(data["start_offset"]),
            end_offset=float(data["end_offset"]),
            start_program_time=(
                datetime.fromisoformat(
                    data["start_program_time"]
                )
                if data["start_program_time"]
                else None
            ),
            end_program_time=(
                datetime.fromisoformat(
                    data["end_program_time"]
                )
                if data["end_program_time"]
                else None
            ),
            duration=float(data["duration"]),
            last_segment_duration=float(
                data["last_segment_duration"]
            ),
            affected_segments=list(
                data["affected_segments"]
            ),
            status=BlackEventStatus(data["status"]),
            long_alert_sent=bool(data["long_alert_sent"]),
            resolution_reason=data.get("resolution_reason"),
            evidence=[
                EventEvidence(
                    evidence_type=item["evidence_type"],
                    strength=EvidenceStrength(item["strength"]),
                    source=item["source"],
                    detail=item["detail"],
                )
                for item in data.get("evidence", [])
            ],
        )
