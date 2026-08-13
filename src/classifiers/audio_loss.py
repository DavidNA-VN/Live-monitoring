from checks.audio_loss.models import (
    AUDIO_DECODE_ERROR,
    AUDIO_PACKET_LOSS,
    AUDIO_TIMESTAMP_ERROR,
    CONTINUOUS_AUDIO_SILENCE,
    INTERMITTENT_AUDIO_LOSS,
    MISSING_AUDIO_STREAM,
    AudioIssue,
    AudioPacketAnalysis,
    AudioSilenceCandidate,
    AudioStreamAnalysis,
)


class AudioLossClassifier:

    def __init__(
        self,
        min_continuous_silence_duration: float = 3.0,
        event_duration_tolerance: float = 0.1,
        intermittent_window: float = 10.0,
        min_dropout_duration: float = 0.5,
        min_dropout_count: int = 3,
        min_packet_loss_packets: int = 3,
        min_packet_loss_ratio: float = 0.05,
    ):
        self.min_continuous_silence_duration = (
            min_continuous_silence_duration
        )
        self.event_duration_tolerance = (
            event_duration_tolerance
        )
        self.intermittent_window = intermittent_window
        self.min_dropout_duration = min_dropout_duration
        self.min_dropout_count = min_dropout_count
        self.min_packet_loss_packets = (
            min_packet_loss_packets
        )
        self.min_packet_loss_ratio = min_packet_loss_ratio

    def classify(
        self,
        variant_id: str,
        stream_results: list[AudioStreamAnalysis],
        packet_results: list[AudioPacketAnalysis],
        silence_candidates: list[AudioSilenceCandidate],
    ) -> list[AudioIssue]:

        issues = []
        issues.extend(
            self._classify_stream_issues(
                variant_id,
                stream_results,
            )
        )
        issues.extend(
            self._classify_packet_loss(
                variant_id,
                packet_results,
                stream_results,
            )
        )
        issues.extend(
            self._classify_continuous_silence(
                silence_candidates
            )
        )
        intermittent_issue = (
            self._classify_intermittent_silence(
                variant_id,
                silence_candidates,
            )
        )

        if intermittent_issue is not None:
            issues.append(
                intermittent_issue
            )

        return issues

    def _classify_packet_loss(
        self,
        variant_id: str,
        packet_results: list[AudioPacketAnalysis],
        stream_results: list[AudioStreamAnalysis],
    ) -> list[AudioIssue]:

        timeline = self._build_timeline(
            stream_results
        )
        issues = []
        current = None

        for result in sorted(
            packet_results,
            key=lambda item: item.sequence,
        ):
            has_issue = (
                result.estimated_missing_packets
                >= self.min_packet_loss_packets
                or result.packet_loss_ratio
                >= self.min_packet_loss_ratio
            )

            if not has_issue:
                if current is not None:
                    issues.append(
                        self._finish_segment_issue(
                            variant_id,
                            AUDIO_PACKET_LOSS,
                            "Audio packet gap/loss detected.",
                            current,
                        )
                    )
                    current = None

                continue

            segment_start, segment_end = timeline.get(
                result.sequence,
                (
                    None,
                    None,
                ),
            )

            if current is None:
                current = {
                    "start_sequence": result.sequence,
                    "end_sequence": result.sequence,
                    "start_time": segment_start,
                    "end_time": segment_end,
                    "segments": [
                        result.sequence
                    ],
                }
            elif (
                result.sequence
                == current["end_sequence"] + 1
            ):
                current["end_sequence"] = result.sequence
                current["end_time"] = segment_end
                current["segments"].append(result.sequence)
            else:
                issues.append(
                    self._finish_segment_issue(
                        variant_id,
                        AUDIO_PACKET_LOSS,
                        "Audio packet gap/loss detected.",
                        current,
                    )
                )
                current = {
                    "start_sequence": result.sequence,
                    "end_sequence": result.sequence,
                    "start_time": segment_start,
                    "end_time": segment_end,
                    "segments": [
                        result.sequence
                    ],
                }

        if current is not None:
            issues.append(
                self._finish_segment_issue(
                    variant_id,
                    AUDIO_PACKET_LOSS,
                    "Audio packet gap/loss detected.",
                    current,
                )
            )

        return issues

    @staticmethod
    def _build_timeline(
        stream_results: list[AudioStreamAnalysis],
    ) -> dict[int, tuple[float, float]]:

        timeline = {}
        offset = 0.0

        for result in sorted(
            stream_results,
            key=lambda item: item.sequence,
        ):
            start = offset
            end = offset + result.segment_duration
            timeline[result.sequence] = (
                start,
                end,
            )
            offset = end

        return timeline

    def _classify_stream_issues(
        self,
        variant_id: str,
        stream_results: list[AudioStreamAnalysis],
    ) -> list[AudioIssue]:

        issues = []

        for issue_type, predicate, message in [
            (
                MISSING_AUDIO_STREAM,
                lambda result: not result.has_audio_stream,
                "Audio stream is missing.",
            ),
            (
                AUDIO_DECODE_ERROR,
                lambda result: result.has_audio_stream
                and not result.decodable,
                "Audio stream cannot be decoded.",
            ),
            (
                AUDIO_TIMESTAMP_ERROR,
                lambda result: bool(
                    result.timestamp_errors
                ),
                "Audio timestamp error detected.",
            ),
        ]:
            issues.extend(
                self._group_segment_issues(
                    variant_id=variant_id,
                    stream_results=stream_results,
                    issue_type=issue_type,
                    predicate=predicate,
                    message=message,
                )
            )

        return issues

    @staticmethod
    def _group_segment_issues(
        variant_id: str,
        stream_results: list[AudioStreamAnalysis],
        issue_type: str,
        predicate,
        message: str,
    ) -> list[AudioIssue]:

        issues = []
        current = None
        timeline_offset = 0.0

        for result in sorted(
            stream_results,
            key=lambda item: item.sequence,
        ):
            has_issue = predicate(result)
            segment_start = timeline_offset
            segment_end = (
                timeline_offset
                + result.segment_duration
            )

            if has_issue:
                if current is None:
                    current = {
                        "start_sequence": result.sequence,
                        "end_sequence": result.sequence,
                        "start_time": segment_start,
                        "end_time": segment_end,
                        "segments": [
                            result.sequence
                        ],
                    }
                elif (
                    result.sequence
                    == current["end_sequence"] + 1
                ):
                    current["end_sequence"] = result.sequence
                    current["end_time"] = segment_end
                    current["segments"].append(result.sequence)
                else:
                    issues.append(
                        AudioLossClassifier
                        ._finish_segment_issue(
                            variant_id,
                            issue_type,
                            message,
                            current,
                        )
                    )
                    current = {
                        "start_sequence": result.sequence,
                        "end_sequence": result.sequence,
                        "start_time": segment_start,
                        "end_time": segment_end,
                        "segments": [
                            result.sequence
                        ],
                    }
            elif current is not None:
                issues.append(
                    AudioLossClassifier
                    ._finish_segment_issue(
                        variant_id,
                        issue_type,
                        message,
                        current,
                    )
                )
                current = None

            timeline_offset = segment_end

        if current is not None:
            issues.append(
                AudioLossClassifier._finish_segment_issue(
                    variant_id,
                    issue_type,
                    message,
                    current,
                )
            )

        return issues

    @staticmethod
    def _finish_segment_issue(
        variant_id: str,
        issue_type: str,
        message: str,
        data,
    ) -> AudioIssue:

        return AudioIssue(
            issue_type=issue_type,
            variant_id=variant_id,
            message=message,
            start_sequence=data["start_sequence"],
            end_sequence=data["end_sequence"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            affected_segments=data["segments"],
        )

    def _classify_continuous_silence(
        self,
        candidates: list[AudioSilenceCandidate],
    ) -> list[AudioIssue]:

        issues = []

        for candidate in candidates:
            if (
                candidate.duration
                + self.event_duration_tolerance
                < self.min_continuous_silence_duration
            ):
                continue

            issues.append(
                AudioIssue(
                    issue_type=CONTINUOUS_AUDIO_SILENCE,
                    variant_id=candidate.variant_id,
                    message=(
                        "Audio stream exists and decodes, "
                        "but continuous silence violates "
                        "business rule."
                    ),
                    start_sequence=candidate.start_sequence,
                    end_sequence=candidate.end_sequence,
                    start_time=candidate.start_time,
                    end_time=candidate.end_time,
                    affected_segments=(
                        candidate.affected_segments
                    ),
                )
            )

        return issues

    def _classify_intermittent_silence(
        self,
        variant_id: str,
        candidates: list[AudioSilenceCandidate],
    ) -> AudioIssue | None:

        dropouts = [
            candidate
            for candidate in candidates
            if (
                candidate.duration
                >= self.min_dropout_duration
                and candidate.duration
                + self.event_duration_tolerance
                < self.min_continuous_silence_duration
            )
        ]

        for index, candidate in enumerate(dropouts):
            window_end = (
                candidate.start_time
                + self.intermittent_window
            )
            window = [
                item
                for item in dropouts[index:]
                if item.start_time <= window_end
            ]

            if len(window) < self.min_dropout_count:
                continue

            affected_segments = sorted(
                {
                    sequence
                    for item in window
                    for sequence in item.affected_segments
                }
            )

            return AudioIssue(
                issue_type=INTERMITTENT_AUDIO_LOSS,
                variant_id=variant_id,
                message=(
                    "Multiple short audio dropouts "
                    "violate intermittent loss rule."
                ),
                start_sequence=affected_segments[0],
                end_sequence=affected_segments[-1],
                start_time=window[0].start_time,
                end_time=window[-1].end_time,
                affected_segments=affected_segments,
            )

        return None
