from dataclasses import dataclass

from models.segment import Segment
from playlist.master_parser import (
    Variant,
    parse_master_playlist,
)
from playlist.media_parser import parse_media_playlist


@dataclass
class MonitoringContext:
    master_url: str
    variants: list[Variant]
    segments_by_variant: dict[str, list[Segment]]

    def segments_for_variant(
        self,
        variant: Variant,
    ) -> list[Segment]:

        return self.segments_by_variant.get(
            variant.id,
            [],
        )


def build_monitoring_context(
    master_url: str,
) -> MonitoringContext:

    variants = parse_master_playlist(
        master_url
    )

    segments_by_variant = {
        variant.id: parse_media_playlist(
            variant
        )
        for variant in variants
    }

    return MonitoringContext(
        master_url=master_url,
        variants=variants,
        segments_by_variant=segments_by_variant,
    )
