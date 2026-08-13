from dataclasses import dataclass
from typing import Optional

import m3u8


@dataclass
class Variant:
    id: str
    uri: str
    bandwidth: Optional[int]
    resolution: Optional[tuple[int, int]]


def parse_master_playlist(master_url: str) -> list[Variant]:
    master = m3u8.load(master_url)

    variants: list[Variant] = []

    for index, playlist in enumerate(master.playlists):
        stream_info = playlist.stream_info

        bandwidth = stream_info.bandwidth
        resolution = stream_info.resolution

        if resolution:
            width, height = resolution

            # 1280x720 -> 720p
            variant_id = f"{height}p"
        else:
            variant_id = f"variant_{index}"

        variants.append(
            Variant(
                id=variant_id,
                uri=playlist.absolute_uri,
                bandwidth=bandwidth,
                resolution=resolution,
            )
        )

    return variants