from checks.audio_loss.models import (
    AudioSilenceCandidate,
    AudioSilenceDetectionResult,
)
from models.segment import Segment


class AudioEventAggregator:

    def __init__(
        self,
        merge_gap_tolerance: float = 0.25,
    ):
        self.merge_gap_tolerance = merge_gap_tolerance

    def aggregate_silence(
        self,
        variant_id: str,
        detection_results: list[
            AudioSilenceDetectionResult
        ],
        segments: list[Segment],
    ) -> list[AudioSilenceCandidate]:

        candidates = []
        current_candidate = None
        timeline_offset = 0.0
        results_by_sequence = {
            result.sequence: result
            for result in detection_results
        }

        for segment in sorted(
            segments,
            key=lambda item: item.sequence,
        ):
            result = results_by_sequence.get(
                segment.sequence
            )

            intervals = (
                result.intervals
                if result is not None
                else []
            )

            for interval in intervals:
                global_start = (
                    timeline_offset
                    + interval.start
                )
                global_end = (
                    timeline_offset
                    + interval.end
                )

                if current_candidate is None:
                    current_candidate = {
                        "start_time": global_start,
                        "end_time": global_end,
                        "start_sequence": result.sequence,
                        "end_sequence": result.sequence,
                        "segments": [
                            result.sequence
                        ],
                    }
                elif (
                    global_start
                    <= current_candidate["end_time"]
                    + self.merge_gap_tolerance
                ):
                    current_candidate["end_time"] = global_end
                    current_candidate[
                        "end_sequence"
                    ] = result.sequence

                    if (
                        result.sequence
                        not in current_candidate["segments"]
                    ):
                        current_candidate[
                            "segments"
                        ].append(result.sequence)
                else:
                    candidates.append(
                        self._finish_candidate(
                            variant_id,
                            current_candidate,
                        )
                    )
                    current_candidate = {
                        "start_time": global_start,
                        "end_time": global_end,
                        "start_sequence": result.sequence,
                        "end_sequence": result.sequence,
                        "segments": [
                            result.sequence
                        ],
                    }

            timeline_offset += segment.duration

        if current_candidate is not None:
            candidates.append(
                self._finish_candidate(
                    variant_id,
                    current_candidate,
                )
            )

        return candidates

    @staticmethod
    def _finish_candidate(
        variant_id: str,
        data,
    ) -> AudioSilenceCandidate:

        duration = data["end_time"] - data["start_time"]

        return AudioSilenceCandidate(
            variant_id=variant_id,
            start_time=data["start_time"],
            end_time=data["end_time"],
            duration=duration,
            start_sequence=data["start_sequence"],
            end_sequence=data["end_sequence"],
            affected_segments=data["segments"],
        )
