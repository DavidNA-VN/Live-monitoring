from types import SimpleNamespace

from playlist import master_parser


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
