import pytest

from core.variant_selector import VariantSelectionError, select_variants
from models.audio import AudioTrackHint
from models.rendition import MediaRenditionKind
from models.variant_selection import (
    VariantSelectionMode,
    VariantSelectionPolicy,
)
from playlist.master_parser import Variant
from tests.factories.hls import make_snapshot


def variant(index: int, *, audio: bool = False) -> Variant:
    return Variant(
        id=f"audio-{index}" if audio else f"{index * 360}p",
        stable_id=f"stable-{index}",
        uri=f"https://example.test/{index}.m3u8",
        bandwidth=None if audio else index * 1_000_000,
        resolution=None if audio else (index * 640, index * 360),
        has_video=not audio,
        audio_track_hint=(
            AudioTrackHint.MUXED if audio else AudioTrackHint.UNKNOWN
        ),
        rendition_kind=(
            MediaRenditionKind.AUDIO
            if audio
            else MediaRenditionKind.VARIANT
        ),
    )


def test_all_preserves_master_order():
    variants = [variant(2), variant(1), variant(3, audio=True)]

    assert select_variants(variants, VariantSelectionPolicy()) == variants


def test_representative_selects_low_mid_high_and_all_external_audio():
    variants = [variant(index) for index in range(1, 7)]
    for item in variants:
        item.audio_group = "main"
    audio = [
        variant(7, audio=True),
        variant(8, audio=True),
    ]
    for item in audio:
        item.audio_group = "main"
    variants += audio
    selected = select_variants(
        variants,
        VariantSelectionPolicy(
            mode=VariantSelectionMode.REPRESENTATIVE,
            representative_count=3,
        ),
    )

    assert [item.id for item in selected[:3]] == ["360p", "1080p", "2160p"]
    assert [item.id for item in selected[3:]] == ["audio-7", "audio-8"]


def test_explicit_accepts_display_and_stable_ids_and_rejects_missing():
    variants = [variant(1), variant(2), variant(3)]
    selected = select_variants(
        variants,
        VariantSelectionPolicy(
            mode="explicit",
            explicit_variant_ids=("360p", "stable-3"),
        ),
    )
    assert [item.id for item in selected] == ["360p", "1080p"]

    with pytest.raises(VariantSelectionError, match="missing"):
        select_variants(
            variants,
            VariantSelectionPolicy(
                mode="explicit",
                explicit_variant_ids=("missing",),
            ),
        )


def test_context_fetches_only_selected_media_playlists(monkeypatch):
    import core.context as context

    variants = [variant(index) for index in range(1, 7)]
    fetched: list[str] = []
    monkeypatch.setattr(
        context,
        "parse_master_playlist",
        lambda *_args, **_kwargs: variants,
    )

    def parse_selected(item, **_kwargs):
        fetched.append(item.id)
        return make_snapshot(
            [100],
            variant_id=item.id,
            variant_stable_id=item.stable_id,
        )

    monkeypatch.setattr(context, "parse_media_playlist", parse_selected)

    result = context.build_monitoring_context(
        "https://example.test/master.m3u8",
        variant_selection=VariantSelectionPolicy(
            mode="representative",
            representative_count=3,
        ),
    )

    assert {item.id for item in result.variants} == {"360p", "1080p", "2160p"}
    assert set(fetched) == {"360p", "1080p", "2160p"}
