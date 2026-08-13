from checks.audio_loss.check import AudioLossCheck
from checks.audio_loss.models import (
    AUDIO_DECODE_ERROR,
    AUDIO_PACKET_LOSS,
    CONTINUOUS_AUDIO_SILENCE,
    INTERMITTENT_AUDIO_LOSS,
    MISSING_AUDIO_STREAM,
    AudioPacketAnalysis,
    AudioPacketGap,
    AudioSilenceDetectionResult,
    AudioSilenceInterval,
    AudioStreamAnalysis,
    AudioStreamInfo,
)
from core.context import MonitoringContext
from models.segment import Segment
from playlist.master_parser import Variant


def make_context() -> MonitoringContext:
    variant = Variant(
        id="720p",
        uri="http://example.test/720p/playlist.m3u8",
        bandwidth=1000,
        resolution=(1280, 720),
    )

    return MonitoringContext(
        master_url="http://example.test/master.m3u8",
        variants=[
            variant,
        ],
        segments_by_variant={
            "720p": [
                Segment(
                    variant_id="720p",
                    sequence=0,
                    uri="http://example.test/720p/0.ts",
                    duration=2.0,
                ),
                Segment(
                    variant_id="720p",
                    sequence=1,
                    uri="http://example.test/720p/1.ts",
                    duration=3.0,
                ),
            ]
        },
    )


def stream_info():
    return [
        AudioStreamInfo(
            index=1,
            codec_name="aac",
            sample_rate="48000",
            channels=2,
        )
    ]


class FakeStreamAnalyzer:
    def __init__(
        self,
        results: list[AudioStreamAnalysis],
    ):
        self.results = results

    def analyze(
        self,
        segments: list[Segment],
    ) -> list[AudioStreamAnalysis]:
        return self.results


class FakeSilenceDetector:
    def __init__(
        self,
        results: dict[int, AudioSilenceDetectionResult],
    ):
        self.results = results

    def detect(
        self,
        segment: Segment,
    ) -> AudioSilenceDetectionResult:
        return self.results.get(
            segment.sequence,
            AudioSilenceDetectionResult(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
            ),
        )


class FakePacketAnalyzer:
    def __init__(
        self,
        results: list[AudioPacketAnalysis],
    ):
        self.results = results

    def analyze(
        self,
        segments: list[Segment],
    ) -> list[AudioPacketAnalysis]:
        by_sequence = {
            result.sequence: result
            for result in self.results
        }

        return [
            by_sequence[segment.sequence]
            for segment in segments
            if segment.sequence in by_sequence
        ]


def stream_result(
    segment: Segment,
    has_audio: bool = True,
    decodable: bool = True,
    decode_errors: list[str] | None = None,
) -> AudioStreamAnalysis:
    return AudioStreamAnalysis(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        has_audio_stream=has_audio,
        decodable=decodable,
        stream_infos=stream_info() if has_audio else [],
        decode_errors=decode_errors or [],
    )


def packet_result(
    segment: Segment,
    missing_packets: int = 0,
    packet_count: int = 100,
) -> AudioPacketAnalysis:
    gaps = []

    if missing_packets:
        gaps.append(
            AudioPacketGap(
                sequence=segment.sequence,
                previous_pts=0.0,
                current_pts=(
                    missing_packets + 1
                )
                * 0.021333,
                expected_duration=0.021333,
                actual_gap=(
                    missing_packets + 1
                )
                * 0.021333,
                estimated_missing_packets=(
                    missing_packets
                ),
            )
        )

    return AudioPacketAnalysis(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        packet_count=packet_count,
        expected_packet_duration=0.021333,
        gaps=gaps,
    )


def test_audio_loss_check_flags_missing_audio():
    context = make_context()
    segments = context.segments_by_variant["720p"]

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(
                    segment,
                    has_audio=False,
                    decodable=False,
                )
                for segment in segments
            ]
        ),
        silence_detector=FakeSilenceDetector({}),
        packet_analyzer=FakePacketAnalyzer([]),
    )

    result = check.run(context)

    issues = result.result["720p"].issues

    assert result.has_issue
    assert len(issues) == 1
    assert issues[0].issue_type == MISSING_AUDIO_STREAM
    assert issues[0].affected_segments == [
        0,
        1,
    ]


def test_audio_loss_check_flags_decode_error():
    context = make_context()
    segments = context.segments_by_variant["720p"]

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(
                    segments[0],
                    decodable=False,
                    decode_errors=[
                        "decode failed"
                    ],
                ),
                stream_result(segments[1]),
            ]
        ),
        silence_detector=FakeSilenceDetector({}),
        packet_analyzer=FakePacketAnalyzer([]),
    )

    result = check.run(context)

    issues = result.result["720p"].issues

    assert result.has_issue
    assert issues[0].issue_type == AUDIO_DECODE_ERROR
    assert issues[0].affected_segments == [
        0,
    ]


def test_audio_loss_continuous_silence_issue_after_aggregation():
    context = make_context()
    segments = context.segments_by_variant["720p"]

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(segment)
                for segment in segments
            ]
        ),
        silence_detector=FakeSilenceDetector(
            {
                0: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=0,
                    segment_uri=segments[0].uri,
                    segment_duration=2.0,
                    intervals=[
                        AudioSilenceInterval(
                            sequence=0,
                            start=0.0,
                            end=2.0,
                        )
                    ],
                ),
                1: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=1,
                    segment_uri=segments[1].uri,
                    segment_duration=3.0,
                    intervals=[
                        AudioSilenceInterval(
                            sequence=1,
                            start=0.0,
                            end=3.0,
                        )
                    ],
                ),
            }
        ),
        packet_analyzer=FakePacketAnalyzer(
            [
                packet_result(segment)
                for segment in segments
            ]
        ),
    )

    result = check.run(context)
    variant_result = result.result["720p"]

    assert result.has_issue
    assert len(variant_result.silence_candidates) == 1
    assert variant_result.silence_candidates[0].duration == 5.0
    assert variant_result.issues[0].issue_type == (
        CONTINUOUS_AUDIO_SILENCE
    )


def test_audio_loss_short_candidate_is_not_issue():
    context = make_context()
    segments = context.segments_by_variant["720p"]

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(segment)
                for segment in segments
            ]
        ),
        silence_detector=FakeSilenceDetector(
            {
                0: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=0,
                    segment_uri=segments[0].uri,
                    segment_duration=2.0,
                    intervals=[
                        AudioSilenceInterval(
                            sequence=0,
                            start=0.0,
                            end=0.8,
                        )
                    ],
                ),
            }
        ),
        packet_analyzer=FakePacketAnalyzer(
            [
                packet_result(segment)
                for segment in segments
            ]
        ),
    )

    result = check.run(context)
    variant_result = result.result["720p"]

    assert not result.has_issue
    assert len(variant_result.silence_candidates) == 1
    assert variant_result.issues == []


def test_audio_loss_interleaved_short_silence_issue():
    variant = Variant(
        id="720p",
        uri="http://example.test/720p/playlist.m3u8",
        bandwidth=1000,
        resolution=(1280, 720),
    )
    segments = [
        Segment(
            variant_id="720p",
            sequence=index,
            uri=f"http://example.test/720p/{index}.ts",
            duration=2.0,
        )
        for index in range(5)
    ]
    context = MonitoringContext(
        master_url="http://example.test/master.m3u8",
        variants=[
            variant,
        ],
        segments_by_variant={
            "720p": segments
        },
    )

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(segment)
                for segment in segments
            ]
        ),
        silence_detector=FakeSilenceDetector(
            {
                0: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=0,
                    segment_uri=segments[0].uri,
                    segment_duration=2.0,
                    intervals=[
                        AudioSilenceInterval(0, 0.5, 1.2)
                    ],
                ),
                2: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=2,
                    segment_uri=segments[2].uri,
                    segment_duration=2.0,
                    intervals=[
                        AudioSilenceInterval(2, 0.4, 1.1)
                    ],
                ),
                4: AudioSilenceDetectionResult(
                    variant_id="720p",
                    sequence=4,
                    segment_uri=segments[4].uri,
                    segment_duration=2.0,
                    intervals=[
                        AudioSilenceInterval(4, 0.2, 0.9)
                    ],
                ),
            }
        ),
        packet_analyzer=FakePacketAnalyzer(
            [
                packet_result(segment)
                for segment in segments
            ]
        ),
    )

    result = check.run(context)

    assert result.has_issue
    assert result.result["720p"].issues[0].issue_type == (
        INTERMITTENT_AUDIO_LOSS
    )


def test_audio_loss_packet_gaps_are_grouped_as_packet_loss():
    variant = Variant(
        id="720p",
        uri="http://example.test/720p/playlist.m3u8",
        bandwidth=1000,
        resolution=(1280, 720),
    )
    segments = [
        Segment(
            variant_id="720p",
            sequence=index,
            uri=f"http://example.test/720p/{index}.ts",
            duration=2.0,
        )
        for index in range(6)
    ]
    context = MonitoringContext(
        master_url="http://example.test/master.m3u8",
        variants=[
            variant,
        ],
        segments_by_variant={
            "720p": segments
        },
    )

    check = AudioLossCheck(
        stream_analyzer=FakeStreamAnalyzer(
            [
                stream_result(segment)
                for segment in segments
            ]
        ),
        silence_detector=FakeSilenceDetector({}),
        packet_analyzer=FakePacketAnalyzer(
            [
                packet_result(segments[0]),
                packet_result(segments[1]),
                packet_result(segments[2]),
                packet_result(
                    segments[3],
                    missing_packets=31,
                    packet_count=62,
                ),
                packet_result(
                    segments[4],
                    missing_packets=31,
                    packet_count=62,
                ),
                packet_result(segments[5]),
            ]
        ),
    )

    result = check.run(context)
    issues = result.result["720p"].issues

    assert result.has_issue
    assert issues[0].issue_type == AUDIO_PACKET_LOSS
    assert issues[0].affected_segments == [
        3,
        4,
    ]
    assert issues[0].start_time == 6.0
    assert issues[0].end_time == 10.0
