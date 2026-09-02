from core.metrics import RuntimeMetricCollector
from models.analysis import (
    AudioRealtimeAnalysis,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.audio import AudioTrackPresence
from models.analysis import AnalysisRequirement
from models.audio import SilenceInterval
from models.freeze import FreezeInterval


def test_metric_collector_drains_async_worker_metrics_per_cycle():
    collector = RuntimeMetricCollector()
    collector.record_analysis(
        duration_seconds=0.4,
        segment_age_seconds=2.0,
        ffmpeg_timed_out=False,
    )
    collector.record_analysis(
        duration_seconds=0.6,
        segment_age_seconds=3.0,
        ffmpeg_timed_out=True,
    )
    collector.record_retry()

    snapshot = collector.drain()

    assert snapshot.analysis_count == 2
    assert snapshot.analysis_duration_seconds_total == 1.0
    assert snapshot.segment_age_seconds_max == 3.0
    assert snapshot.ffmpeg_timeout_total == 1
    assert snapshot.retry_total == 1
    assert collector.drain().analysis_count == 0


def test_audio_operational_metrics_distinguish_loss_from_analysis_failure():
    collector = RuntimeMetricCollector()
    collector.record_profile_result(
        SegmentAnalysisBundle(
            profile_name="audio_realtime",
            audio_realtime=AudioRealtimeAnalysis(
                checked=True,
                presence=AudioTrackPresence.PRESENT,
                outputs={
                    AnalysisRequirement.SILENCE_INTERVALS: (
                        SilenceInterval(start=0.0, end=1.5),
                    )
                },
            ),
        )
    )
    collector.record_profile_result(
        SegmentAnalysisBundle(
            profile_name="audio_realtime",
            audio_realtime=AudioRealtimeAnalysis(
                checked=True,
                presence=AudioTrackPresence.ABSENT,
            ),
        )
    )
    collector.record_profile_result(
        SegmentAnalysisBundle(
            profile_name="audio_realtime",
            audio_realtime=AudioRealtimeAnalysis(
                checked=False,
                presence=AudioTrackPresence.UNKNOWN,
                timed_out=True,
            ),
        )
    )

    snapshot = collector.drain()

    assert snapshot.audio_analysis_total == 3
    assert snapshot.audio_analysis_failure_total == 1
    assert snapshot.audio_analysis_timeout_total == 1
    assert snapshot.audio_track_missing_total == 1
    assert snapshot.audio_silence_seconds_total == 1.5


def test_video_operational_metrics_distinguish_freeze_from_analysis_failure():
    collector = RuntimeMetricCollector()
    collector.record_profile_result(
        SegmentAnalysisBundle(
            profile_name="video_realtime",
            video_realtime=VideoRealtimeAnalysis(
                checked=True,
                outputs={
                    AnalysisRequirement.FREEZE_INTERVALS: (
                        FreezeInterval(start=0.5, end=2.0),
                        FreezeInterval(start=3.0, end=4.0),
                    )
                },
            ),
        )
    )
    collector.record_profile_result(
        SegmentAnalysisBundle(
            profile_name="video_realtime",
            video_realtime=VideoRealtimeAnalysis(
                checked=False,
                timed_out=True,
            ),
        )
    )

    snapshot = collector.drain()

    assert snapshot.profile_metrics == {
        "video_analysis_total": 2,
        "video_analysis_failure_total": 1,
        "video_analysis_timeout_total": 1,
        "video_freeze_interval_total": 2,
        "video_freeze_seconds_total": 2.5,
    }


def test_analysis_bundle_reports_timeout_from_either_media_profile():
    video = SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=False,
            timed_out=True,
        ),
    )
    audio = SegmentAnalysisBundle(
        profile_name="audio_realtime",
        audio_realtime=AudioRealtimeAnalysis(
            checked=False,
            presence=AudioTrackPresence.UNKNOWN,
            timed_out=True,
        ),
    )

    assert video.media_process_timed_out is True
    assert audio.media_process_timed_out is True
