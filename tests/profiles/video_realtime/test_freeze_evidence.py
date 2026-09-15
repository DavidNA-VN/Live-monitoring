from __future__ import annotations

import pytest

from models.detection import BlackInterval
from models.freeze import FreezeInterval
from core.frame_similarity import make_gray_fingerprint
from profiles.video_realtime.freeze_evidence import FreezeEvidenceAssembler


FIRST = make_gray_fingerprint(bytes([80] * (32 * 32)))
LAST = make_gray_fingerprint(bytes([90] * (32 * 32)))


def assemble(freeze, black=(), *, duration=6.0, minimum=0.2):
    return FreezeEvidenceAssembler(minimum_duration=minimum).assemble(
        raw_freeze_intervals=freeze,
        black_intervals=black,
        segment_duration=duration,
        first_frame=FIRST,
        last_frame=LAST,
    )


def test_black_only_freeze_interval_is_excluded():
    assert assemble(
        (FreezeInterval(0.0, 6.0),),
        (BlackInterval(0.0, 6.0),),
    ) == ()


def test_partial_black_overlap_splits_freeze_into_non_black_pieces():
    result = assemble(
        (FreezeInterval(0.0, 6.0),),
        (BlackInterval(2.0, 4.0),),
    )

    assert [(item.start, item.end) for item in result] == [
        (0.0, 2.0),
        (4.0, 6.0),
    ]
    assert result[0].start_boundary_fingerprint == FIRST
    assert result[0].end_boundary_fingerprint is None
    assert result[1].start_boundary_fingerprint is None
    assert result[1].end_boundary_fingerprint == LAST


def test_small_remainder_after_black_subtraction_is_discarded():
    result = assemble(
        (FreezeInterval(0.0, 1.0),),
        (BlackInterval(0.1, 1.0),),
        minimum=0.2,
    )
    assert result == ()


def test_effective_intervals_are_sorted_and_do_not_overlap():
    result = assemble(
        (FreezeInterval(3.0, 5.0), FreezeInterval(0.0, 2.0)),
        (BlackInterval(1.0, 1.5),),
    )
    assert [(item.start, item.end) for item in result] == [
        (0.0, 1.0),
        (1.5, 2.0),
        (3.0, 5.0),
    ]


def test_overlapping_raw_intervals_are_merged_before_exclusion():
    result = assemble(
        (FreezeInterval(0.0, 3.0), FreezeInterval(2.0, 5.0)),
    )
    assert [(item.start, item.end) for item in result] == [(0.0, 5.0)]


@pytest.mark.parametrize("minimum", [0.0, -1.0, float("nan")])
def test_invalid_minimum_duration_is_rejected(minimum):
    with pytest.raises(ValueError, match="minimum_duration"):
        FreezeEvidenceAssembler(minimum_duration=minimum)
