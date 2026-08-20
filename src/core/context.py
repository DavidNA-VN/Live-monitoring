from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed

from models.playlist_snapshot import (
    MediaPlaylistSnapshot,
)
from models.segment import Segment
from playlist.errors import PlaylistLoadError
from playlist.master_parser import (
    Variant,
    parse_master_playlist,
)
from playlist.media_parser import (
    parse_media_playlist,
)


@dataclass
class MonitoringContext:
    """
    One observation cycle of a live HLS master playlist.

    Long-lived processing/event state must not be stored here.
    """

    master_url: str

    observed_at: datetime

    variants: list[Variant]

    snapshots_by_variant: dict[
        str,
        MediaPlaylistSnapshot,
    ]

    snapshot_errors_by_variant: dict[
        str,
        str,
    ]

    def snapshot_for_variant(
        self,
        variant: Variant,
    ) -> MediaPlaylistSnapshot | None:

        return self.snapshots_by_variant.get(
            variant.id
        )

    def segments_for_variant(
        self,
        variant: Variant,
    ) -> list[Segment]:

        snapshot = self.snapshot_for_variant(
            variant
        )

        if snapshot is None:
            return []

        return snapshot.segments


def build_monitoring_context(
    master_url: str,
    playlist_timeout: float = 5.0,
    request_headers: Mapping[str, str] | None = None,
    media_playlist_workers: int = 4,
) -> MonitoringContext:

    if media_playlist_workers <= 0:
        raise ValueError("media_playlist_workers must be > 0")

    cycle_observed_at = datetime.now(
        timezone.utc
    )

    variants = parse_master_playlist(
        master_url,
        timeout=playlist_timeout,
        request_headers=request_headers,
    )

    snapshots_by_variant: dict[
        str,
        MediaPlaylistSnapshot,
    ] = {}

    snapshot_errors_by_variant: dict[
        str,
        str,
    ] = {}

    worker_count = min(media_playlist_workers, len(variants))
    if worker_count:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="media-playlist-fetch",
        ) as executor:
            futures = {
                executor.submit(
                    parse_media_playlist,
                    variant,
                    timeout=playlist_timeout,
                    request_headers=request_headers,
                ): variant
                for variant in variants
            }
            for future in as_completed(futures):
                variant = futures[future]
                try:
                    snapshot = future.result()
                except (PlaylistLoadError, ValueError) as exc:
                    snapshot_errors_by_variant[variant.id] = str(exc)
                    continue
                snapshots_by_variant[variant.id] = snapshot

    return MonitoringContext(
        master_url=master_url,
        observed_at=cycle_observed_at,
        variants=variants,
        snapshots_by_variant=(
            snapshots_by_variant
        ),
        snapshot_errors_by_variant=(
            snapshot_errors_by_variant
        ),
    )
