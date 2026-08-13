from checks.audio_loss.models import (
    AudioLossVariantResult,
)
from checks.black_screen.check import (
    BlackScreenVariantResult,
)
from checks.freeze_frame.check import (
    FreezeFrameVariantResult,
)
from core.monitor import MonitorReport
from models.detection import BlackDetectionResult


def print_monitor_report(
    report: MonitorReport,
) -> None:

    print(
        f"\nFound {len(report.context.variants)} variants"
    )

    for check_name, check_result in report.results.items():
        print()
        print("#" * 70)
        print(f"CHECK: {check_name}")
        print("#" * 70)

        if check_name == "black_screen":
            print_black_screen_results(
                check_result.result
            )
        elif check_name == "freeze_frame":
            print_freeze_frame_results(
                check_result.result
            )
        elif check_name == "audio_loss":
            print_audio_loss_results(
                check_result.result
            )
        else:
            print(check_result.result)


def print_black_screen_results(
    results: dict[str, BlackScreenVariantResult],
) -> None:

    for variant_result in results.values():
        print_black_variant_result(
            variant_result
        )


def print_freeze_frame_results(
    results: dict[str, FreezeFrameVariantResult],
) -> None:

    for variant_result in results.values():
        print_freeze_variant_result(
            variant_result
        )


def print_audio_loss_results(
    results: dict[str, AudioLossVariantResult],
) -> None:

    for variant_result in results.values():
        print_audio_loss_variant_result(
            variant_result
        )


def print_black_detection_result(
    result: BlackDetectionResult,
) -> None:

    if not result.has_black:
        print(
            f"[NORMAL] "
            f"seq={result.sequence:<5} "
            f"duration={result.segment_duration:.3f}s"
        )

        return

    intervals_text = ", ".join(
        (
            f"{interval.start:.3f}"
            f" -> "
            f"{interval.end:.3f}s"
        )
        for interval in result.black_intervals
    )

    print(
        f"[BLACK ] "
        f"seq={result.sequence:<5} "
        f"duration={result.segment_duration:.3f}s "
        f"intervals=[{intervals_text}] "
        f"total_black={result.total_black_duration:.3f}s"
    )


def print_black_variant_result(
    variant_result: BlackScreenVariantResult,
) -> None:

    variant = variant_result.variant

    print()
    print("=" * 70)
    print(f"Variant    : {variant.id}")
    print(f"Resolution : {variant.resolution}")
    print(f"Bandwidth  : {variant.bandwidth}")
    print(f"Playlist   : {variant.uri}")
    print("=" * 70)

    print(
        f"Segments   : "
        f"{len(variant_result.detection_results)}"
    )

    print()

    for result in variant_result.detection_results:
        print_black_detection_result(
            result
        )

    print()
    print("BLACK SCREEN EVENTS")
    print("-" * 70)

    if not variant_result.events:
        print(
            "No black screen event detected."
        )

        return

    for index, event in enumerate(
        variant_result.events,
        start=1,
    ):

        print(f"Event #{index}")

        print(
            f"  Variant          : "
            f"{event.variant_id}"
        )

        print(
            f"  Start sequence   : "
            f"{event.start_sequence}"
        )

        print(
            f"  End sequence     : "
            f"{event.end_sequence}"
        )

        print(
            f"  Start offset     : "
            f"{event.start_offset:.3f}s"
        )

        print(
            f"  End offset       : "
            f"{event.end_offset:.3f}s"
        )

        print(
            f"  Global start     : "
            f"{event.start_time:.3f}s"
        )

        print(
            f"  Global end       : "
            f"{event.end_time:.3f}s"
        )

        print(
            f"  Duration         : "
            f"{event.duration:.3f}s"
        )

        print(
            f"  Affected segments: "
            f"{event.affected_segments}"
        )

        if index <= len(variant_result.event_analyses):
            analysis = (
                variant_result.event_analyses[
                    index - 1
                ]
            )

            print(
                f"  Classification   : "
                f"{analysis.classification}"
            )

            print(
                f"  Technical score  : "
                f"{analysis.technical_score:.2f}"
            )

            print(
                f"  Confidence       : "
                f"{analysis.confidence:.2f}"
            )

            print(
                f"  Bitstream error  : "
                f"{analysis.bitstream.has_bitstream_error}"
            )
            print(
                f"  Bitstream failed : "
                f"{analysis.bitstream.failed_segment_count}"
            )

            if analysis.audio is not None:
                print(
                    f"  Audio checked    : "
                    f"{analysis.audio.checked}"
                )
                print(
                    f"  Has audio        : "
                    f"{analysis.audio.has_audio}"
                )
                print(
                    f"  Audio active     : "
                    f"{analysis.audio.audio_active_during_black}"
                )
                print(
                    f"  Silence ratio    : "
                    f"{analysis.audio.silence_ratio:.2f}"
                )

            if analysis.transition is not None:
                print(
                    f"  Fade out/in      : "
                    f"{analysis.transition.fade_out}/"
                    f"{analysis.transition.fade_in}"
                )
                print(
                    f"  Abrupt start/end : "
                    f"{analysis.transition.abrupt_start}/"
                    f"{analysis.transition.abrupt_end}"
                )
                print(
                    f"  Boundary jumps   : "
                    f"{analysis.transition.start_boundary_jump}/"
                    f"{analysis.transition.end_boundary_jump}"
                )

            if analysis.cross_variant is not None:
                print(
                    f"  Variant overlap  : "
                    f"{analysis.cross_variant.overlapping_variant_ids}"
                )
                print(
                    f"  Min overlap ratio: "
                    f"{analysis.cross_variant.min_overlap_ratio:.2f}"
                )
                print(
                    f"  All variants     : "
                    f"{analysis.cross_variant.all_variants_affected}"
                )

            for check in (
                analysis.bitstream.segment_checks
            ):
                if not check.checked:
                    status = "CHECK_FAILED"
                elif check.has_error:
                    status = "ERROR"
                else:
                    status = "OK"

                print(
                    f"    seq={check.sequence:<5} "
                    f"{status}"
                )

                if check.analyzer_error:
                    print(
                        f"      {check.analyzer_error}"
                    )

                for line in check.error_lines[:3]:
                    print(
                        f"      {line}"
                    )

        print()


def print_freeze_variant_result(
    variant_result: FreezeFrameVariantResult,
) -> None:

    variant = variant_result.variant

    print()
    print("=" * 70)
    print(f"Variant    : {variant.id}")
    print(f"Resolution : {variant.resolution}")
    print(f"Bandwidth  : {variant.bandwidth}")
    print(f"Playlist   : {variant.uri}")
    print("=" * 70)

    print()
    print("FREEZE FRAME EVENTS")
    print("-" * 70)

    if not variant_result.events:

        print(
            "No freeze frame event detected."
        )

        return

    for index, event in enumerate(
        variant_result.events,
        start=1,
    ):

        print(f"Event #{index}")

        print(
            f"  Variant          : "
            f"{event.variant_id}"
        )

        print(
            f"  Start sequence   : "
            f"{event.start_sequence}"
        )

        print(
            f"  End sequence     : "
            f"{event.end_sequence}"
        )

        print(
            f"  Start offset     : "
            f"{event.start_offset:.3f}s"
        )

        print(
            f"  End offset       : "
            f"{event.end_offset:.3f}s"
        )

        print(
            f"  Global start     : "
            f"{event.start_time:.3f}s"
        )

        print(
            f"  Global end       : "
            f"{event.end_time:.3f}s"
        )

        print(
            f"  Duration         : "
            f"{event.duration:.3f}s"
        )

        print(
            f"  Affected segments: "
            f"{event.affected_segments}"
        )

        print()


def print_audio_loss_variant_result(
    variant_result: AudioLossVariantResult,
) -> None:

    variant = variant_result.variant

    print()
    print("=" * 70)
    print(f"Variant    : {variant.id}")
    print(f"Resolution : {variant.resolution}")
    print(f"Bandwidth  : {variant.bandwidth}")
    print(f"Playlist   : {variant.uri}")
    print("=" * 70)

    print(
        f"Checked duration: "
        f"{variant_result.checked_duration:.3f}s"
    )
    print(
        f"Has issue       : "
        f"{variant_result.has_issue}"
    )

    print()
    print("AUDIO STREAM ANALYSIS")
    print("-" * 70)

    for result in variant_result.stream_results:
        if not result.has_audio_stream:
            status = "MISSING_AUDIO_STREAM"
        elif not result.decodable:
            status = "AUDIO_DECODE_ERROR"
        elif result.timestamp_errors:
            status = "AUDIO_TIMESTAMP_ERROR"
        else:
            status = "OK"

        print(
            f"seq={result.sequence:<5} "
            f"{status:<22} "
            f"duration={result.segment_duration:.3f}s"
        )

        for stream in result.stream_infos:
            print(
                f"  stream index={stream.index} "
                f"codec={stream.codec_name} "
                f"sample_rate={stream.sample_rate} "
                f"channels={stream.channels}"
            )

        for line in result.decode_errors[:2]:
            print(
                f"  decode: {line}"
            )

        for line in result.timestamp_errors[:2]:
            print(
                f"  timestamp: {line}"
            )

    print()
    print("AUDIO PACKET ANALYSIS")
    print("-" * 70)

    if not variant_result.packet_results:
        print(
            "No audio packet analysis available."
        )
    else:
        for result in variant_result.packet_results:
            if not result.checked:
                status = "CHECK_FAILED"
            elif result.gaps:
                status = "PACKET_LOSS"
            else:
                status = "OK"

            print(
                f"seq={result.sequence:<5} "
                f"{status:<22} "
                f"packet_count={result.packet_count}"
            )

            if result.error:
                print(
                    f"  error                  : "
                    f"{result.error}"
                )

            if result.expected_packet_duration is not None:
                print(
                    f"  expected_packet_duration: "
                    f"{result.expected_packet_duration:.6f}s"
                )

            if result.gaps:
                print(
                    f"  estimated_missing      : "
                    f"{result.estimated_missing_packets}"
                )
                print(
                    f"  loss_ratio             : "
                    f"{result.packet_loss_ratio:.2%}"
                )

            for gap in result.gaps[:3]:
                print(
                    f"  gap {gap.previous_pts:.6f}s -> "
                    f"{gap.current_pts:.6f}s "
                    f"actual={gap.actual_gap:.6f}s "
                    f"missing~{gap.estimated_missing_packets}"
                )

    print()
    print("AUDIO SILENCE CANDIDATES")
    print("-" * 70)

    if not variant_result.silence_candidates:
        print(
            "No audio silence candidate detected."
        )
    else:
        for index, candidate in enumerate(
            variant_result.silence_candidates,
            start=1,
        ):
            print(f"Candidate #{index}")
            print(
                f"  Variant          : "
                f"{candidate.variant_id}"
            )
            print(
                f"  Global start     : "
                f"{candidate.start_time:.3f}s"
            )
            print(
                f"  Global end       : "
                f"{candidate.end_time:.3f}s"
            )
            print(
                f"  Duration         : "
                f"{candidate.duration:.3f}s"
            )
            print(
                f"  Affected segments: "
                f"{candidate.affected_segments}"
            )

    print()
    print("AUDIO ISSUES")
    print("-" * 70)

    if not variant_result.issues:
        print("No audio issue detected.")
        return

    for issue in variant_result.issues:
        location = ""

        if issue.start_sequence is not None:
            location = (
                f"seq={issue.start_sequence}"
                f"->{issue.end_sequence} "
            )

        if issue.start_time is not None:
            location = (
                f"{location}"
                f"{issue.start_time:.3f}s -> "
                f"{issue.end_time:.3f}s "
            )

        print(
            f"{location}{issue.issue_type}: "
            f"{issue.message}"
        )
