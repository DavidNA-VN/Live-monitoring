from datetime import datetime, timedelta, timezone

from core.context import MonitoringContext
from core.live_polling import calculate_live_edge_lag, minimum_target_duration
from models.playlist_snapshot import MediaPlaylistSnapshot
from models.segment import Segment


NOW = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def _snapshot(
    variant_id: str,
    *,
    segment_end_lag: float | None,
) -> MediaPlaylistSnapshot:
    duration = 2.0
    program_date_time = (
        None
        if segment_end_lag is None
        else NOW - timedelta(seconds=segment_end_lag + duration)
    )
    return MediaPlaylistSnapshot(
        variant_id=variant_id,
        variant_stable_id=variant_id,
        playlist_uri=f"https://example.test/{variant_id}.m3u8",
        media_sequence=100,
        discontinuity_sequence=0,
        target_duration=duration,
        playlist_type=None,
        is_endlist=False,
        observed_at=NOW,
        segments=[
            Segment(
                variant_id=variant_id,
                variant_stable_id=variant_id,
                sequence=100,
                uri=f"https://example.test/{variant_id}/100.ts",
                duration=duration,
                program_date_time=program_date_time,
            )
        ],
    )


def _context(*snapshots: MediaPlaylistSnapshot) -> MonitoringContext:
    return MonitoringContext(
        master_url="https://example.test/master.m3u8",
        observed_at=NOW,
        variants=[],
        snapshots_by_variant={item.variant_id: item for item in snapshots},
        snapshot_errors_by_variant={},
    )


def test_live_edge_lag_reports_slowest_variant() -> None:
    context = _context(
        _snapshot("fast", segment_end_lag=1.5),
        _snapshot("slow", segment_end_lag=7.0),
    )

    assert calculate_live_edge_lag(context, now=NOW) == 7.0


def test_live_edge_lag_is_unknown_without_program_date_time() -> None:
    context = _context(_snapshot("variant", segment_end_lag=None))

    assert calculate_live_edge_lag(context, now=NOW) is None


def test_live_edge_lag_clamps_future_media_to_zero() -> None:
    context = _context(_snapshot("variant", segment_end_lag=-1.0))

    assert calculate_live_edge_lag(context, now=NOW) == 0.0


def test_minimum_target_duration_uses_fastest_playlist_cadence() -> None:
    fast = _snapshot("fast", segment_end_lag=1.0)
    slow = _snapshot("slow", segment_end_lag=1.0)
    fast.target_duration = 2.0
    slow.target_duration = 6.0

    assert minimum_target_duration(_context(fast, slow)) == 2.0


def test_minimum_target_duration_is_unknown_without_valid_targets() -> None:
    snapshot = _snapshot("variant", segment_end_lag=1.0)
    snapshot.target_duration = None

    assert minimum_target_duration(_context(snapshot)) is None
