from dataclasses import dataclass, field

from models.detection import BlackDetectionResult


@dataclass
class BlackScreenEvent:
    variant_id: str

    start_sequence: int
    end_sequence: int

    start_offset: float
    end_offset: float

    start_time: float
    end_time: float

    duration: float

    affected_segments: list[int] = field(
        default_factory=list
    )


class BlackEventAggregator:

    def aggregate(
        self,
        results: list[BlackDetectionResult],
    ) -> list[BlackScreenEvent]:

        if not results:
            return []

        # Bắt buộc xử lý đúng timeline
        results = sorted(
            results,
            key=lambda result: result.sequence,
        )

        events: list[BlackScreenEvent] = []

        current_event = None

        timeline_offset = 0.0

        for result in results:

            segment_start_time = timeline_offset

            if not result.has_black:
                if current_event is not None:
                    events.append(
                        self._finish_event(
                            current_event
                        )
                    )
                    current_event = None

                timeline_offset += (
                    result.segment_duration
                )

                continue

            for interval in result.black_intervals:

                global_start = (
                    segment_start_time
                    + interval.start
                )

                global_end = (
                    segment_start_time
                    + interval.end
                )

                if current_event is None:
                    current_event = {
                        "variant_id": (
                            result.variant_id
                        ),
                        "start_sequence": (
                            result.sequence
                        ),
                        "end_sequence": (
                            result.sequence
                        ),
                        "start_offset": (
                            interval.start
                        ),
                        "end_offset": (
                            interval.end
                        ),
                        "start_time": (
                            global_start
                        ),
                        "end_time": (
                            global_end
                        ),
                        "segments": [
                            result.sequence
                        ],
                    }

                else:
                    if self._is_continuous(
                        current_event=current_event,
                        result=result,
                        interval=interval,
                    ):
                        current_event[
                            "end_sequence"
                        ] = result.sequence

                        current_event[
                            "end_offset"
                        ] = interval.end

                        current_event[
                            "end_time"
                        ] = global_end

                        if (
                            result.sequence
                            not in current_event[
                                "segments"
                            ]
                        ):
                            current_event[
                                "segments"
                            ].append(
                                result.sequence
                            )

                    else:
                        events.append(
                            self._finish_event(
                                current_event
                            )
                        )

                        current_event = {
                            "variant_id": (
                                result.variant_id
                            ),
                            "start_sequence": (
                                result.sequence
                            ),
                            "end_sequence": (
                                result.sequence
                            ),
                            "start_offset": (
                                interval.start
                            ),
                            "end_offset": (
                                interval.end
                            ),
                            "start_time": (
                                global_start
                            ),
                            "end_time": (
                                global_end
                            ),
                            "segments": [
                                result.sequence
                            ],
                        }

            timeline_offset += (
                result.segment_duration
            )

        if current_event is not None:
            events.append(
                self._finish_event(
                    current_event
                )
            )

        return events

    @staticmethod
    def _is_continuous(
        current_event,
        result,
        interval,
    ) -> bool:
        """
        Event được coi là liên tục nếu:

        - cùng variant
        - sequence hiện tại nối tiếp sequence trước
        - black bắt đầu gần đầu segment

        0.1s tolerance để tránh sai số frame/timestamp.
        """

        same_variant = (
            current_event["variant_id"]
            == result.variant_id
        )

        next_sequence = (
            result.sequence
            == current_event["end_sequence"] + 1
        )

        starts_near_segment_start = (
            interval.start <= 0.1
        )

        return (
            same_variant
            and next_sequence
            and starts_near_segment_start
        )

    @staticmethod
    def _finish_event(
        event_data,
    ) -> BlackScreenEvent:

        duration = (
            event_data["end_time"]
            - event_data["start_time"]
        )

        return BlackScreenEvent(
            variant_id=(
                event_data["variant_id"]
            ),
            start_sequence=(
                event_data["start_sequence"]
            ),
            end_sequence=(
                event_data["end_sequence"]
            ),
            start_offset=(
                event_data["start_offset"]
            ),
            end_offset=(
                event_data["end_offset"]
            ),
            start_time=(
                event_data["start_time"]
            ),
            end_time=(
                event_data["end_time"]
            ),
            duration=duration,
            affected_segments=(
                event_data["segments"]
            ),
        )