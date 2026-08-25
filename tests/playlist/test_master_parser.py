from types import SimpleNamespace

from playlist import master_parser
from models.audio import AudioTrackHint
from models.rendition import MediaRenditionKind


def playlist(
    bandwidth: int,
    resolution: tuple[int, int] | None,
    uri: str,
    codecs: str | None = None,
):
    return SimpleNamespace(
        stream_info=SimpleNamespace(
            bandwidth=bandwidth,
            resolution=resolution,
            codecs=codecs,
            audio=None,
            frame_rate=None,
        ),
        absolute_uri=uri,
    )


def test_parse_master_playlist_keeps_unique_resolution_id_when_no_collision(
    monkeypatch,
):
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[
                playlist(
                    bandwidth=1_000_000,
                    resolution=(1280, 720),
                    uri="http://example.test/720p.m3u8",
                ),
            ]
        ),
    )

    variants = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert [
        variant.id
        for variant in variants
    ] == ["720p"]


def test_parse_master_playlist_makes_duplicate_resolution_ids_unique(
    monkeypatch,
):
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[
                playlist(
                    bandwidth=3_000_000,
                    resolution=(1280, 720),
                    uri="http://example.test/720p-high.m3u8",
                ),
                playlist(
                    bandwidth=2_000_000,
                    resolution=(1280, 720),
                    uri="http://example.test/720p-low.m3u8",
                ),
            ]
        ),
    )

    variants = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert [
        variant.id
        for variant in variants
    ] == [
        "720p",
        "720p_2",
    ]


def test_parse_master_playlist_marks_audio_only_variant(
    monkeypatch,
):
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[
                playlist(
                    bandwidth=128_000,
                    resolution=None,
                    codecs="mp4a.40.2",
                    uri="http://example.test/audio.m3u8",
                ),
                playlist(
                    bandwidth=2_000_000,
                    resolution=(1280, 720),
                    codecs="avc1.64001f,mp4a.40.2",
                    uri="http://example.test/video.m3u8",
                ),
            ]
        ),
    )

    variants = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert variants[0].has_video is False
    assert variants[1].has_video is True
    assert variants[0].audio_track_hint is AudioTrackHint.MUXED
    assert variants[1].audio_track_hint is AudioTrackHint.MUXED


def test_parse_master_playlist_infers_audio_track_hints(monkeypatch):
    external = playlist(
        bandwidth=2_000_000,
        resolution=(1280, 720),
        codecs="avc1.64001f,mp4a.40.2",
        uri="http://example.test/external.m3u8",
    )
    external.stream_info.audio = "main-audio"
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[
                playlist(
                    bandwidth=2_000_000,
                    resolution=(1280, 720),
                    codecs="avc1.64001f",
                    uri="http://example.test/video-only.m3u8",
                ),
                playlist(
                    bandwidth=2_000_000,
                    resolution=(1280, 720),
                    codecs=None,
                    uri="http://example.test/unknown.m3u8",
                ),
                external,
            ],
            media=[
                SimpleNamespace(
                    type="AUDIO",
                    group_id="main-audio",
                    name="Main",
                    language=None,
                    default="YES",
                    autoselect="YES",
                    absolute_uri="http://example.test/main-audio.m3u8",
                    stable_rendition_id=None,
                )
            ],
        ),
    )

    variants = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert variants[0].audio_track_hint is AudioTrackHint.ABSENT
    assert variants[1].audio_track_hint is AudioTrackHint.UNKNOWN
    assert variants[2].audio_track_hint is AudioTrackHint.EXTERNAL


def test_master_playlist_forwards_request_headers(
    monkeypatch,
):
    calls = []

    def fake_load(uri, **options):
        calls.append((uri, options))
        return SimpleNamespace(playlists=[])

    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        fake_load,
    )

    master_parser.parse_master_playlist(
        "http://example.test/master.m3u8",
        request_headers={
            "Authorization": "Bearer token"
        },
    )

    assert calls[0][1]["headers"] == {
        "Authorization": "Bearer token"
    }


def test_external_audio_group_becomes_one_schedulable_rendition(monkeypatch):
    first = playlist(
        bandwidth=2_000_000,
        resolution=(1280, 720),
        codecs="avc1.64001f,mp4a.40.2",
        uri="http://example.test/720p.m3u8",
    )
    second = playlist(
        bandwidth=4_000_000,
        resolution=(1920, 1080),
        codecs="avc1.640028,mp4a.40.2",
        uri="http://example.test/1080p.m3u8",
    )
    first.stream_info.audio = "main"
    second.stream_info.audio = "main"
    audio = SimpleNamespace(
        type="AUDIO",
        group_id="main",
        name="English",
        language="en",
        default="YES",
        autoselect="YES",
        absolute_uri="http://example.test/audio/en.m3u8",
        stable_rendition_id="audio-en-v1",
    )
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[first, second],
            media=[audio, audio],
        ),
    )

    renditions = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert len(renditions) == 3
    external = renditions[2]
    assert external.rendition_kind is MediaRenditionKind.AUDIO
    assert external.id == "audio:main:English"
    assert external.has_video is False
    assert external.audio_track_hint is AudioTrackHint.MUXED
    assert external.audio_group == "main"
    assert external.language == "en"
    assert external.is_default is True
    assert external.autoselect is True
    assert external.hls_stable_rendition_id == "audio-en-v1"
    assert renditions[0].audio_track_hint is AudioTrackHint.EXTERNAL
    assert renditions[1].audio_track_hint is AudioTrackHint.EXTERNAL


def test_audio_group_without_uri_remains_muxed_in_variant(monkeypatch):
    video = playlist(
        bandwidth=2_000_000,
        resolution=(1280, 720),
        codecs="avc1.64001f,mp4a.40.2",
        uri="http://example.test/720p.m3u8",
    )
    video.stream_info.audio = "in-band"
    monkeypatch.setattr(
        master_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            playlists=[video],
            media=[
                SimpleNamespace(
                    type="AUDIO",
                    group_id="in-band",
                    name="Main",
                    language="en",
                    default="YES",
                    autoselect="YES",
                    absolute_uri=None,
                    stable_rendition_id=None,
                )
            ],
        ),
    )

    renditions = master_parser.parse_master_playlist(
        "http://example.test/master.m3u8"
    )

    assert len(renditions) == 1
    assert renditions[0].audio_track_hint is AudioTrackHint.MUXED


def test_hls_stable_rendition_id_survives_uri_rotation(monkeypatch):
    video = playlist(
        bandwidth=2_000_000,
        resolution=(1280, 720),
        codecs="avc1.64001f,mp4a.40.2",
        uri="http://example.test/video.m3u8",
    )
    video.stream_info.audio = "main"
    current_uri = ["http://cdn-a.test/en.m3u8?token=one"]

    def load(_uri, timeout):
        return SimpleNamespace(
            playlists=[video],
            media=[
                SimpleNamespace(
                    type="AUDIO",
                    group_id="main",
                    name="English",
                    language="en",
                    default="YES",
                    autoselect="YES",
                    absolute_uri=current_uri[0],
                    stable_rendition_id="stable-audio-en",
                )
            ],
        )

    monkeypatch.setattr(master_parser.m3u8, "load", load)
    first = master_parser.parse_master_playlist("http://example.test/master")[1]
    current_uri[0] = "http://cdn-b.test/rotated.m3u8?token=two"
    second = master_parser.parse_master_playlist("http://example.test/master")[1]

    assert first.stable_id == second.stable_id
