import pytest

from scripts.generate_video_freeze_fixtures import (
    FIXTURES,
    FRAME_RATE,
    FreezeRange,
    _frame_index,
    _freeze_filter_graph,
)


def test_fixture_matrix_covers_business_boundaries_and_cross_segment_case():
    by_name = {item.name: item for item in FIXTURES}

    assert by_name["freeze_2_9s"].expected_public_severity is None
    assert by_name["freeze_3_2s"].expected_public_severity == "WARNING"
    assert by_name["freeze_5_2s"].expected_public_severity == "ALERT"
    assert by_name["freeze_5_2s"].expected_event_type == "VIDEO_FREEZE"
    cross = by_name["freeze_cross_three_segments"].freezes[0]
    assert cross.start < 2.0
    assert cross.end > 6.0
    assert len(by_name["three_warning_freezes"].freezes) == 3
    assert (
        by_name["three_warning_freezes"].expected_event_type
        == "REPEATED_VIDEO_FREEZE"
    )


def test_freezeframes_filter_uses_end_exclusive_timeline():
    interval = FreezeRange(2.0, 5.2)

    expression = _freeze_filter_graph((interval,))

    assert FRAME_RATE == 10
    assert expression == (
        "[0:v]split=2[main0][ref0];"
        "[main0][ref0]freezeframes="
        "first=20:last=51:replace=20[vout]"
    )
    assert _frame_index(interval.end) - _frame_index(interval.start) == 32


def test_multiple_freezes_are_chained_without_extra_decode_inputs():
    expression = _freeze_filter_graph(
        (FreezeRange(2.0, 5.2), FreezeRange(8.0, 11.2))
    )

    assert expression is not None
    assert expression.startswith("[0:v]split=2")
    assert "[stage0]split=2[main1][ref1]" in expression
    assert expression.endswith("replace=80[vout]")


def test_motion_only_fixture_does_not_build_complex_filter():
    assert _freeze_filter_graph(()) is None


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1.0, 2.0), (2.0, 2.0), (2.0, float("inf"))],
)
def test_freeze_range_rejects_invalid_timeline(start, end):
    with pytest.raises(ValueError, match="freeze"):
        FreezeRange(start, end)
