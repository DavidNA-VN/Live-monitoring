import pytest

from models.freeze import FreezeInterval
from profiles.video_realtime.parser.freezedetect import FreezedetectParser
from tests.factories.hls import make_segment


@pytest.fixture
def segment():
    return make_segment(100, duration=6.0)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ordinary ffmpeg output", []),
        (
            "\n".join(
                (
                    "lavfi.freezedetect.freeze_start: 1.2",
                    "lavfi.freezedetect.freeze_duration: 2.5",
                    "lavfi.freezedetect.freeze_end: 3.7",
                )
            ),
            [(1.2, 3.7)],
        ),
        (
            "\n".join(
                (
                    "freeze_start: 0.4",
                    "freeze_end: 1.0",
                    "freeze_start: 2.1",
                    "freeze_end: 3.8",
                )
            ),
            [(0.4, 1.0), (2.1, 3.8)],
        ),
        ("freeze_start: 4.25", [(4.25, 6.0)]),
        ("freeze_start: 4 freeze_end: 7.5", [(4.0, 6.0)]),
        ("freeze_start: -0.1 freeze_end: 1e0", [(0.0, 1.0)]),
        ("freeze_end: 2.0", []),
        ("freeze_start: invalid freeze_end: 2.0", []),
        ("freeze_start: 3 freeze_end: 3", []),
    ],
)
def test_parse_freeze_intervals(segment, output, expected):
    intervals = FreezedetectParser().parse(
        ffmpeg_output=output,
        segment=segment,
    )

    assert [(item.start, item.end) for item in intervals] == expected


def test_filter_expression_uses_candidate_not_business_threshold():
    parser = FreezedetectParser(noise_db=-55.0, minimum_duration=0.25)

    assert parser.filter_expression == "freezedetect=n=-55dB:d=0.25"


@pytest.mark.parametrize("noise", [0.1, float("inf"), float("nan")])
def test_parser_rejects_invalid_noise(noise):
    with pytest.raises(ValueError, match="noise_db"):
        FreezedetectParser(noise_db=noise)


@pytest.mark.parametrize(
    "duration", [0.0, -1.0, float("inf"), float("nan")]
)
def test_parser_rejects_invalid_minimum_duration(duration):
    with pytest.raises(ValueError, match="minimum_duration"):
        FreezedetectParser(minimum_duration=duration)


@pytest.mark.parametrize(
    ("start", "end"),
    [(-0.1, 1.0), (1.0, 1.0), (1.0, float("inf"))],
)
def test_freeze_interval_rejects_invalid_bounds(start, end):
    with pytest.raises(ValueError, match="freeze interval"):
        FreezeInterval(start, end)


def test_freeze_interval_is_immutable_and_has_exact_duration():
    interval = FreezeInterval(1.2, 3.7)

    assert interval.duration == pytest.approx(2.5)
    with pytest.raises(Exception):
        interval.start = 0.0
