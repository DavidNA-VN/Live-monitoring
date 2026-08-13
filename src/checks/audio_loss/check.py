from core.context import MonitoringContext
from core.monitor import CheckResult
from analyzers.audio_packet import AudioPacketAnalyzer
from analyzers.audio_stream import AudioStreamAnalyzer
from classifiers.audio_loss import AudioLossClassifier
from detectors.audio_silence import AudioSilenceDetector
from events.audio_event_aggregator import AudioEventAggregator

from checks.audio_loss.models import (
    AudioLossVariantResult,
)


class AudioLossCheck:
    name = "audio_loss"

    def __init__(
        self,
        stream_analyzer: AudioStreamAnalyzer | None = None,
        silence_detector: AudioSilenceDetector | None = None,
        packet_analyzer: AudioPacketAnalyzer | None = None,
        event_aggregator: AudioEventAggregator | None = None,
        classifier: AudioLossClassifier | None = None,
    ):
        self.stream_analyzer = (
            stream_analyzer
            or AudioStreamAnalyzer()
        )
        self.silence_detector = (
            silence_detector
            or AudioSilenceDetector()
        )
        self.packet_analyzer = (
            packet_analyzer
            or AudioPacketAnalyzer()
        )
        self.event_aggregator = (
            event_aggregator
            or AudioEventAggregator()
        )
        self.classifier = (
            classifier
            or AudioLossClassifier()
        )

    def run(
        self,
        context: MonitoringContext,
    ) -> CheckResult:

        results = self.run_raw(
            context
        )

        return CheckResult(
            name=self.name,
            result=results,
            has_issue=any(
                result.has_issue
                for result in results.values()
            ),
        )

    def run_raw(
        self,
        context: MonitoringContext,
    ) -> dict[str, AudioLossVariantResult]:

        results = {}

        for variant in context.variants:
            segments = context.segments_for_variant(
                variant
            )
            stream_results = self.stream_analyzer.analyze(
                segments
            )
            decodable_segments = [
                segment
                for segment, stream_result in zip(
                    segments,
                    stream_results,
                )
                if (
                    stream_result.has_audio_stream
                    and stream_result.decodable
                )
            ]
            detection_results = [
                self.silence_detector.detect(segment)
                for segment in decodable_segments
            ]
            packet_results = (
                self.packet_analyzer.analyze(
                    decodable_segments
                )
            )
            silence_candidates = (
                self.event_aggregator.aggregate_silence(
                    variant_id=variant.id,
                    detection_results=detection_results,
                    segments=segments,
                )
            )
            issues = self.classifier.classify(
                variant_id=variant.id,
                stream_results=stream_results,
                packet_results=packet_results,
                silence_candidates=silence_candidates,
            )

            results[variant.id] = AudioLossVariantResult(
                variant=variant,
                checked_duration=sum(
                    segment.duration
                    for segment in segments
                ),
                stream_results=stream_results,
                packet_results=packet_results,
                silence_candidates=silence_candidates,
                issues=issues,
            )

        return results
