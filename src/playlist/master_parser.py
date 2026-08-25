from dataclasses import dataclass
from hashlib import sha256
from typing import Optional
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit
from playlist.errors import PlaylistLoadError
import m3u8

from models.audio import AudioTrackHint
from models.rendition import MediaRenditionKind


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
    audio_track_hint: AudioTrackHint = AudioTrackHint.UNKNOWN
    rendition_kind: MediaRenditionKind = MediaRenditionKind.VARIANT
    rendition_name: str | None = None
    language: str | None = None
    is_default: bool = False
    autoselect: bool = False
    hls_stable_rendition_id: str | None = None


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


def _hls_yes(value: object) -> bool:
    return value is True or str(value).upper() == "YES"


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


def _infer_audio_track_hint(
    codecs: str | None,
    audio_group: str | None,
) -> AudioTrackHint:
    if audio_group:
        return AudioTrackHint.EXTERNAL

    if not codecs:
        return AudioTrackHint.UNKNOWN

    codec_names = [
        value.strip().lower()
        for value in codecs.split(",")
        if value.strip()
    ]

    if any(
        codec.startswith(_AUDIO_CODEC_PREFIXES)
        for codec in codec_names
    ):
        return AudioTrackHint.MUXED

    if codec_names and all(
        codec.startswith(_VIDEO_CODEC_PREFIXES)
        for codec in codec_names
    ):
        return AudioTrackHint.ABSENT

    return AudioTrackHint.UNKNOWN


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


def _stable_audio_rendition_id(
    *,
    uri: str,
    group_id: str,
    hls_stable_rendition_id: str | None = None,
) -> str:
    parsed = urlsplit(uri)
    stable_uri = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )
    identity = (
        f"audio|stable|{group_id}|{hls_stable_rendition_id}"
        if hls_stable_rendition_id
        else f"audio|uri|{stable_uri}|{group_id}"
    )
    return sha256(identity.encode("utf-8")).hexdigest()[:24]


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
    referenced_audio_groups: set[str] = set()
    master_media = tuple(getattr(master, "media", ()))
    external_audio_groups = {
        str(getattr(media, "group_id", "") or "")
        for media in master_media
        if str(getattr(media, "type", "")).upper() == "AUDIO"
        and getattr(media, "absolute_uri", None)
    }

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
        if audio_group:
            referenced_audio_groups.add(str(audio_group))
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
                audio_track_hint=_infer_audio_track_hint(
                    codecs,
                    (
                        audio_group
                        if audio_group in external_audio_groups
                        else None
                    ),
                ),
            )
        )

    known_audio_ids: set[str] = set()
    for media in master_media:
        if str(getattr(media, "type", "")).upper() != "AUDIO":
            continue
        group_id = str(getattr(media, "group_id", "") or "")
        uri = getattr(media, "absolute_uri", None)
        if not group_id or group_id not in referenced_audio_groups or not uri:
            continue
        name = str(getattr(media, "name", "") or "audio")
        language = getattr(media, "language", None)
        hls_stable_rendition_id = getattr(
            media,
            "stable_rendition_id",
            None,
        )
        stable_id = _stable_audio_rendition_id(
            uri=uri,
            group_id=group_id,
            hls_stable_rendition_id=hls_stable_rendition_id,
        )
        if stable_id in known_audio_ids:
            continue
        known_audio_ids.add(stable_id)
        base_id = f"audio:{group_id}:{name}"
        occurrence = used_ids.get(base_id, 0)
        used_ids[base_id] = occurrence + 1
        rendition_id = (
            base_id if occurrence == 0 else f"{base_id}_{occurrence + 1}"
        )
        variants.append(
            Variant(
                id=rendition_id,
                stable_id=stable_id,
                uri=uri,
                bandwidth=None,
                resolution=None,
                codecs=None,
                audio_group=group_id,
                has_video=False,
                audio_track_hint=AudioTrackHint.MUXED,
                rendition_kind=MediaRenditionKind.AUDIO,
                rendition_name=name,
                language=language,
                is_default=_hls_yes(getattr(media, "default", False)),
                autoselect=_hls_yes(getattr(media, "autoselect", False)),
                hls_stable_rendition_id=hls_stable_rendition_id,
            )
        )

    return variants
