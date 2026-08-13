from models.black_analysis import (
    AudioEvidence,
    BitstreamEvidence,
    BlackEventAnalysis,
    CrossVariantEvidence,
    TransitionEvidence,
)
from events.black_event_aggregator import BlackScreenEvent


TECHNICAL_ERROR = "BLACK_SCREEN_TECHNICAL_ERROR"
SUSPECTED_TECHNICAL_BLACK = (
    "SUSPECTED_TECHNICAL_BLACK"
)
POSSIBLE_INTENTIONAL_BLACK = (
    "POSSIBLE_INTENTIONAL_BLACK"
)
NEEDS_CONTEXT = (
    "BLACK_SCREEN_CANDIDATE_NEEDS_CONTEXT"
)


class BlackScreenClassifier:

    def classify(
        self,
        event: BlackScreenEvent,
        bitstream: BitstreamEvidence,
        audio: AudioEvidence | None = None,
        transition: TransitionEvidence | None = None,
        cross_variant: CrossVariantEvidence | None = None,
    ) -> BlackEventAnalysis:

        technical_score = self._technical_score(
            event=event,
            bitstream=bitstream,
            audio=audio,
            transition=transition,
            cross_variant=cross_variant,
        )

        if (
            bitstream.has_bitstream_error
            and technical_score >= 0.70
        ):
            classification = TECHNICAL_ERROR
        elif technical_score >= 0.70:
            classification = SUSPECTED_TECHNICAL_BLACK
        elif technical_score <= 0.30:
            classification = POSSIBLE_INTENTIONAL_BLACK
        else:
            classification = NEEDS_CONTEXT

        confidence = abs(technical_score - 0.5) * 2

        return BlackEventAnalysis(
            variant_id=event.variant_id,
            start_time=event.start_time,
            end_time=event.end_time,
            affected_segments=event.affected_segments,
            classification=classification,
            technical_score=technical_score,
            confidence=confidence,
            bitstream=bitstream,
            audio=audio,
            transition=transition,
            cross_variant=cross_variant,
        )

    @staticmethod
    def _technical_score(
        event: BlackScreenEvent,
        bitstream: BitstreamEvidence,
        audio: AudioEvidence | None,
        transition: TransitionEvidence | None,
        cross_variant: CrossVariantEvidence | None,
    ) -> float:

        score = 0.35

        score += BlackScreenClassifier._duration_score(
            event.duration
        )

        if bitstream.has_bitstream_error:
            score += 0.25

        if audio is not None and audio.checked:
            if audio.audio_active_during_black:
                score += 0.20
            elif audio.has_audio and audio.silence_ratio >= 0.80:
                score -= 0.10

        if transition is not None and transition.checked:
            if transition.abrupt_start:
                score += 0.10
            if transition.abrupt_end:
                score += 0.10
            if transition.fade_out and transition.fade_in:
                score -= 0.20

        if (
            cross_variant is not None
            and cross_variant.checked
        ):
            if cross_variant.only_this_variant_affected:
                score += 0.15

        return min(
            1.0,
            max(
                0.0,
                score,
            ),
        )

    @staticmethod
    def _duration_score(
        duration: float,
    ) -> float:

        if duration < 1.0:
            return 0.0

        if duration < 3.0:
            return 0.02

        if duration < 5.0:
            return 0.05

        if duration < 10.0:
            return 0.10

        return 0.15
