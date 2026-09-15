from __future__ import annotations

from models.rendition import MediaRenditionKind
from models.variant_selection import (
    VariantSelectionMode,
    VariantSelectionPolicy,
)
from playlist.master_parser import Variant


class VariantSelectionError(ValueError):
    pass


def select_variants(
    variants: list[Variant],
    policy: VariantSelectionPolicy,
) -> list[Variant]:
    if policy.mode is VariantSelectionMode.ALL:
        return list(variants)

    if policy.mode is VariantSelectionMode.EXPLICIT:
        requested = set(policy.explicit_variant_ids)
        selected = [
            variant
            for variant in variants
            if variant.id in requested or variant.stable_id in requested
        ]
        matched = {
            candidate
            for variant in selected
            for candidate in (variant.id, variant.stable_id)
            if candidate in requested
        }
        missing = requested - matched
        if missing:
            raise VariantSelectionError(
                "Explicit variants not found: " + ", ".join(sorted(missing))
            )
        return selected

    video = [
        variant
        for variant in variants
        if variant.rendition_kind is MediaRenditionKind.VARIANT
        and variant.has_video
    ]
    audio = [
        variant
        for variant in variants
        if variant.rendition_kind is MediaRenditionKind.AUDIO
    ]
    if policy.mode is VariantSelectionMode.HIGHEST_QUALITY:
        if not video:
            raise VariantSelectionError("No video variant is available")
        selected_video = max(video, key=_quality_key)
        selected_audio = _preferred_audio_rendition(
            audio,
            group_id=selected_video.audio_group,
        )
        return [selected_video] + (
            [selected_audio] if selected_audio is not None else []
        )

    ranked = sorted(video, key=_quality_key)
    selected_video = _evenly_spaced(ranked, policy.representative_count)
    selected_audio_groups = {
        variant.audio_group
        for variant in selected_video
        if variant.audio_group is not None
    }
    selected_audio = [
        variant
        for variant in audio
        if variant.audio_group in selected_audio_groups
    ]
    return selected_video + selected_audio


def _quality_key(variant: Variant) -> tuple[int, int, str]:
    pixels = (
        variant.resolution[0] * variant.resolution[1]
        if variant.resolution is not None
        else 0
    )
    return (pixels, variant.bandwidth or 0, variant.stable_id)


def _preferred_audio_rendition(
    variants: list[Variant],
    *,
    group_id: str | None,
) -> Variant | None:
    if group_id is None:
        return None
    candidates = [
        variant for variant in variants if variant.audio_group == group_id
    ]
    for candidate in candidates:
        if candidate.is_default:
            return candidate
    for candidate in candidates:
        if candidate.autoselect:
            return candidate
    return candidates[0] if candidates else None


def _evenly_spaced(variants: list[Variant], count: int) -> list[Variant]:
    if len(variants) <= count:
        return variants
    if count == 1:
        return [variants[len(variants) // 2]]
    indexes = {
        round(index * (len(variants) - 1) / (count - 1))
        for index in range(count)
    }
    return [variants[index] for index in sorted(indexes)]
