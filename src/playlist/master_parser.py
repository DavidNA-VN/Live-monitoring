from dataclasses import dataclass
from hashlib import sha256
from typing import Optional
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit
from playlist.errors import PlaylistLoadError
import m3u8


@dataclass
class Variant:
    # Human-readable ID used for display/reporting.
    id: str

    # Stable machine identity used by live runtime / Redis.
    stable_id: str

    uri: str
    bandwidth: Optional[int]
    resolution: Optional[tuple[int, int]]
    codecs: str | None = None
    audio_group: str | None = None
    frame_rate: float | None = None
    has_video: bool = True


_VIDEO_CODEC_PREFIXES = (
    "avc1",
    "avc3",
    "hev1",
    "hvc1",
    "vp09",
    "av01",
    "dvh1",
    "dvhe",
)

_AUDIO_CODEC_PREFIXES = (
    "mp4a",
    "ac-3",
    "ec-3",
    "opus",
    "flac",
)


def _infer_has_video(
    resolution: tuple[int, int] | None,
    codecs: str | None,
) -> bool:
    if resolution is not None:
        return True

    if not codecs:
        # Unknown is kept eligible. FFmpeg will make the final check.
        return True

    codec_names = [
        value.strip().lower()
        for value in codecs.split(",")
        if value.strip()
    ]

    if any(
        codec.startswith(_VIDEO_CODEC_PREFIXES)
        for codec in codec_names
    ):
        return True

    return not (
        codec_names
        and all(
            codec.startswith(_AUDIO_CODEC_PREFIXES)
            for codec in codec_names
        )
    )


def _stable_variant_id(
    uri: str,
    bandwidth: int | None,
    resolution: tuple[int, int] | None,
) -> str:
    """
    Build a stable opaque identity for one HLS rendition.

    Query/fragment are intentionally excluded because live CDN URLs
    may rotate authentication tokens while still referring to the
    same rendition resource.
    """

    parsed = urlsplit(uri)

    stable_uri = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            "",
            "",
        )
    )

    identity = (
        f"{stable_uri}|"
        f"{bandwidth}|"
        f"{resolution}"
    )

    return sha256(
        identity.encode("utf-8")
    ).hexdigest()[:24]


def parse_master_playlist(
    master_url: str,
    timeout: float = 5.0,
    request_headers: Mapping[str, str] | None = None,
) -> list[Variant]:

    try:
        load_options = {
            "timeout": timeout,
        }

        if request_headers:
            load_options["headers"] = dict(
                request_headers
            )

        master = m3u8.load(
            master_url,
            **load_options,
        )
    except (
        OSError,
        ValueError,
        m3u8.ParseError,
    ) as exc:
        raise PlaylistLoadError(
            uri=master_url,
            message=str(exc),
    ) from exc

    variants: list[Variant] = []
    used_ids: dict[str, int] = {}

    for index, playlist in enumerate(
        master.playlists
    ):
        stream_info = playlist.stream_info

        bandwidth = stream_info.bandwidth
        resolution = stream_info.resolution
        codecs = getattr(
            stream_info,
            "codecs",
            None,
        )
        audio_group = getattr(
            stream_info,
            "audio",
            None,
        )
        raw_frame_rate = getattr(
            stream_info,
            "frame_rate",
            None,
        )
        frame_rate = (
            float(raw_frame_rate)
            if raw_frame_rate is not None
            else None
        )

        if resolution:
            _, height = resolution
            base_id = f"{height}p"
        else:
            base_id = f"variant_{index}"

        occurrence = used_ids.get(
            base_id,
            0,
        )

        used_ids[base_id] = occurrence + 1

        variant_id = (
            base_id
            if occurrence == 0
            else f"{base_id}_{occurrence + 1}"
        )

        absolute_uri = playlist.absolute_uri

        variants.append(
            Variant(
                id=variant_id,
                stable_id=_stable_variant_id(
                    uri=absolute_uri,
                    bandwidth=bandwidth,
                    resolution=resolution,
                ),
                uri=absolute_uri,
                bandwidth=bandwidth,
                resolution=resolution,
                codecs=codecs,
                audio_group=audio_group,
                frame_rate=frame_rate,
                has_video=_infer_has_video(
                    resolution,
                    codecs,
                ),
            )
        )

    return variants
