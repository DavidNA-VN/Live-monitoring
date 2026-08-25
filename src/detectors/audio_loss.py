from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.audio import AudioTrackPresence, SilenceInterval
from models.audio_loss import AudioLossDetectionResult, AudioLossSignal
from models.segment import Segment


class AudioLossDetector:
    def detect(
        self,
        *,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> AudioLossDetectionResult:
        audio = analysis.require_audio_realtime()

        if not audio.checked:
            return self._result(
                segment,
                presence=AudioTrackPresence.UNKNOWN,
                signal=AudioLossSignal.UNKNOWN,
                checked=False,
                error=audio.error or "audio analysis failed",
                retryable=audio.retryable,
            )

        if audio.presence is AudioTrackPresence.ABSENT:
            return self._result(
                segment,
                presence=AudioTrackPresence.ABSENT,
                signal=AudioLossSignal.MISSING,
            )

        if audio.presence is not AudioTrackPresence.PRESENT:
            return self._result(
                segment,
                presence=AudioTrackPresence.UNKNOWN,
                signal=AudioLossSignal.UNKNOWN,
                checked=False,
                error=audio.error or "audio presence is unknown",
                retryable=audio.retryable,
            )

        intervals = audio.require_output(
            AnalysisRequirement.SILENCE_INTERVALS,
            tuple,
        )
        if not all(isinstance(item, SilenceInterval) for item in intervals):
            raise TypeError("Invalid silence_intervals analysis output")

        return self._result(
            segment,
            presence=AudioTrackPresence.PRESENT,
            signal=(
                AudioLossSignal.SILENT
                if intervals
                else AudioLossSignal.AUDIBLE
            ),
            intervals=list(intervals),
        )

    @staticmethod
    def _result(
        segment: Segment,
        *,
        presence: AudioTrackPresence,
        signal: AudioLossSignal,
        checked: bool = True,
        error: str | None = None,
        retryable: bool = True,
        intervals: list[SilenceInterval] | None = None,
    ) -> AudioLossDetectionResult:
        return AudioLossDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            program_date_time=segment.program_date_time,
            track_presence=presence,
            signal=signal,
            checked=checked,
            error=error,
            retryable=retryable,
            silence_intervals=list(intervals or ()),
        )
