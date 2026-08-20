from __future__ import annotations

from urllib.parse import urlsplit

from models.playlist_delta import (
    MediaPlaylistDelta,
    MissedSequenceRange,
    SegmentReplacement,
)
from models.playlist_snapshot import (
    MediaPlaylistSnapshot,
)
from models.segment import Segment


class PlaylistDeltaError(ValueError):
    pass


class PlaylistDeltaEngine:

    def __init__(
        self,
        pdt_tolerance: float = 0.050,
        duration_tolerance: float = 0.050,
    ):
        if pdt_tolerance < 0:
            raise ValueError(
                "pdt_tolerance must be >= 0"
            )

        if duration_tolerance < 0:
            raise ValueError(
                "duration_tolerance must be >= 0"
            )

        self.pdt_tolerance = pdt_tolerance
        self.duration_tolerance = (
            duration_tolerance
        )

    def compare(
        self,
        previous: MediaPlaylistSnapshot,
        current: MediaPlaylistSnapshot,
    ) -> MediaPlaylistDelta:

        self._validate_snapshots(
            previous=previous,
            current=current,
        )

        timeline_reset, reset_reason = (
            self._detect_timeline_reset(
                previous=previous,
                current=current,
            )
        )

        previous_by_sequence = {
            segment.sequence: segment
            for segment in previous.segments
        }

        current_by_sequence = {
            segment.sequence: segment
            for segment in current.segments
        }

        retained_segments: list[Segment] = []
        replaced_segments: list[
            SegmentReplacement
        ] = []
        new_segments: list[Segment] = []

        if timeline_reset:
            # Sequence/discontinuity domain moved backwards.
            #
            # Do not trust sequence-number overlap with the previous
            # snapshot. The current snapshot begins a new observation
            # timeline from the delta engine's perspective.
            new_segments = list(
                current.segments
            )

        else:
            for segment in current.segments:
                previous_segment = (
                    previous_by_sequence.get(
                        segment.sequence
                    )
                )

                if previous_segment is None:
                    new_segments.append(
                        segment
                    )
                    continue

                equivalent, reason = (
                    self._segments_equivalent(
                        previous=previous_segment,
                        current=segment,
                    )
                )

                if equivalent:
                    retained_segments.append(
                        segment
                    )
                    continue

                replaced_segments.append(
                    SegmentReplacement(
                        previous=previous_segment,
                        current=segment,
                        reason=reason,
                    )
                )

        removed_sequences = sorted(
            sequence
            for sequence
            in previous_by_sequence
            if sequence
            not in current_by_sequence
        )

        missed_sequence_ranges = (
            []
            if timeline_reset
            else self._detect_missed_sequences(
                previous=previous,
                current=current,
            )
        )

        declared_gap_segments = [
            segment
            for segment in current.segments
            if segment.gap
        ]

        return MediaPlaylistDelta(
            variant_id=current.variant_id,
            previous_observed_at=(
                previous.observed_at
            ),
            current_observed_at=(
                current.observed_at
            ),
            new_segments=new_segments,
            retained_segments=(
                retained_segments
            ),
            replaced_segments=(
                replaced_segments
            ),
            removed_sequences=(
                removed_sequences
            ),
            declared_gap_segments=(
                declared_gap_segments
            ),
            missed_sequence_ranges=(
                missed_sequence_ranges
            ),
            timeline_reset=timeline_reset,
            timeline_reset_reason=(
                reset_reason
            ),
            timeline_conflict=bool(
                replaced_segments
            ),
        )

    @staticmethod
    def _validate_snapshots(
        previous: MediaPlaylistSnapshot,
        current: MediaPlaylistSnapshot,
    ) -> None:

        if previous.variant_id != current.variant_id:
            raise PlaylistDeltaError(
                (
                    "Cannot compare snapshots from "
                    "different variants: "
                    f"{previous.variant_id!r} != "
                    f"{current.variant_id!r}"
                )
            )

        if (
            previous.variant_stable_id
            != current.variant_stable_id
        ):
            raise PlaylistDeltaError(
                (
                    "Cannot compare snapshots from "
                    "different variant identities."
                )
            )

        if (
            current.observed_at
            < previous.observed_at
        ):
            raise PlaylistDeltaError(
                (
                    "Current snapshot was observed "
                    "before previous snapshot."
                )
            )

    @staticmethod
    def _detect_timeline_reset(
        previous: MediaPlaylistSnapshot,
        current: MediaPlaylistSnapshot,
    ) -> tuple[bool, str | None]:

        if (
            current.media_sequence
            < previous.media_sequence
        ):
            return (
                True,
                "media_sequence_regressed",
            )

        if (
            current.discontinuity_sequence
            < previous.discontinuity_sequence
        ):
            return (
                True,
                "discontinuity_sequence_regressed",
            )

        return (
            False,
            None,
        )

    def _segments_equivalent(
        self,
        previous: Segment,
        current: Segment,
    ) -> tuple[bool, str]:

        if (
            previous.discontinuity_sequence
            != current.discontinuity_sequence
        ):
            return (
                False,
                "discontinuity_sequence_changed",
            )

        if (
            previous.program_date_time is not None
            and current.program_date_time is not None
        ):
            difference = abs(
                (
                    current.program_date_time
                    - previous.program_date_time
                ).total_seconds()
            )

            if difference > self.pdt_tolerance:
                return (
                    False,
                    "program_date_time_changed",
                )

        if (
            abs(
                previous.duration
                - current.duration
            )
            > self.duration_tolerance
        ):
            return (
                False,
                "duration_changed",
            )

        if previous.byte_range != current.byte_range:
            return (
                False,
                "byte_range_changed",
            )

        if not self._init_sections_equivalent(
            previous,
            current,
        ):
            return (
                False,
                "init_section_changed",
            )

        if not self._encryption_equivalent(
            previous,
            current,
        ):
            return (
                False,
                "encryption_changed",
            )

        if (
            self._stable_resource_identity(
                previous.uri
            )
            != self._stable_resource_identity(
                current.uri
            )
        ):
            return (
                False,
                "segment_resource_changed",
            )

        if previous.gap != current.gap:
            return (
                False,
                "gap_state_changed",
            )

        return (
            True,
            "equivalent",
        )

    @staticmethod
    def _stable_resource_identity(
        uri: str,
    ) -> tuple[str, str, str]:

        parsed = urlsplit(uri)

        # Ignore query and fragment deliberately.
        #
        # Signed CDN URLs may rotate authentication tokens while still
        # pointing to the same HLS media resource.
        return (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
        )

    @classmethod
    def _init_sections_equivalent(
        cls,
        previous: Segment,
        current: Segment,
    ) -> bool:
        previous_init = previous.init_section
        current_init = current.init_section

        if previous_init is None or current_init is None:
            return previous_init is current_init

        return (
            previous_init.byte_range
            == current_init.byte_range
            and cls._stable_resource_identity(
                previous_init.uri
            )
            == cls._stable_resource_identity(
                current_init.uri
            )
        )

    @classmethod
    def _encryption_equivalent(
        cls,
        previous: Segment,
        current: Segment,
    ) -> bool:
        previous_encryption = previous.encryption
        current_encryption = current.encryption

        if (
            previous_encryption is None
            or current_encryption is None
        ):
            return (
                previous_encryption
                is current_encryption
            )

        previous_key = (
            cls._stable_resource_identity(
                previous_encryption.key_uri
            )
            if previous_encryption.key_uri
            else None
        )
        current_key = (
            cls._stable_resource_identity(
                current_encryption.key_uri
            )
            if current_encryption.key_uri
            else None
        )

        return (
            previous_encryption.method
            == current_encryption.method
            and previous_encryption.iv
            == current_encryption.iv
            and previous_encryption.key_format
            == current_encryption.key_format
            and previous_key == current_key
        )

    @staticmethod
    def _detect_missed_sequences(
        previous: MediaPlaylistSnapshot,
        current: MediaPlaylistSnapshot,
    ) -> list[MissedSequenceRange]:

        previous_last = previous.last_sequence
        current_first = current.first_sequence

        if (
            previous_last is None
            or current_first is None
        ):
            return []

        if current_first <= previous_last + 1:
            return []

        return [
            MissedSequenceRange(
                start_sequence=(
                    previous_last + 1
                ),
                end_sequence=(
                    current_first - 1
                ),
            )
        ]
