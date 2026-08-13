from __future__ import annotations
import sys
import m3u8
import time

from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}
DEFAULT_URL = ("https://ec04-pop4-hlc.tv360.vn/bpk-token/czlcuekmqi6bch4gja6rovrkcelfmkqr/bpk-tv/249/output/249-audio_198800_eng_iv_3=196800-video_iv_3=5256400.m3u8")

def load_media_playlist(url: str) -> m3u8.M3U8:
    playlist = m3u8.load(
        url, 
        timeout=10,
        headers = HEADERS,
    )
    if playlist.is_variant:
        raise ValueError(
            "URL la master playlist"
        )
    return playlist
def get_segments_sequence (playlist: m3u8.M3U8) -> list[tuple[int, m3u8.M3U8]]:
    result = []
    for offset,segment in enumerate(playlist.segments):
        sequence = playlist.media_sequence + offset
        result.append((sequence,segment))
    return result
def print_segment(sequence: int, segment: m3u8.Segment, prefix: str = "") -> None:
    print(
        f"{prefix}"
        f"sequence = {sequence} | "
        f"duration = {segment.duration:.3f}s | "
        f"uri = {segment.uri} | "
    )

def main() -> None:
    url = DEFAULT_URL
    previous_last_sequence: int | None = None
    reload_count = 0
    reload_interval = 4.0
    print("Watching Media Playlist:")
    print(url)
    print("Ctrl+C to stop")
    try:
        while True:
            reload_count += 1
            request_started = time.monotonic()

            try:
                playlist = load_media_playlist(url)
                if playlist.target_duration:
                    reload_interval = float(
                        playlist.target_duration
                    )
                segments = get_segments_sequence(playlist)
                current_time = datetime.now().strftime("%H:%M:%S")
                print(
                    f"\n[{current_time}]"
                    f"Reload #{reload_count}"
                )

                print(
                    "Media sequence:",
                    playlist.media_sequence,

                )
                print(
                    "Target duration:",
                    playlist.target_duration,
                )
                print(
                    "Number of segments:",
                    len(playlist.segments),
                )
                current_first_sequence = segments[0][0]
                current_last_sequence = segments[-1][0]
                print (
                    "Current window:",
                    f"{current_first_sequence}"
                    f" -> {current_last_sequence}",
                )
                if previous_last_sequence is None:
                    print("Initial segment:")
                    for sequence, segment in segments:
                        print_segment(
                            sequence,
                            segment,
                            prefix = " [CURRENT]",
                        )
                elif current_last_sequence < previous_last_sequence:
                    print(
                        "[WARNING] Media Sequence decreased!"
                    )
                    print(
                        "Previous last sequence:",
                        previous_last_sequence,
                    
                    )
                    print(
                        "Current last sequence:",
                        current_last_sequence,
                    )
                    print(
                        "Stream may have restarted or reset."
                    )
                    for sequence, segment in segments:
                        print_segment(
                            sequence,
                            segment,
                            prefix= " [RESET]",
                        )
                else:
                    new_segments = [
                        (sequence, segment)
                        for sequence, segment in segments
                        if sequence > previous_last_sequence
                    ]
                    if not new_segments:
                        print("No new segments.")
                    else:
                        expected_next = (
                            previous_last_sequence + 1
                        )
                        first_new_sequence = new_segments[0][0]
                        if first_new_sequence > expected_next:
                            missing_count = (
                                first_new_sequence - expected_next
                            )
                            print(
                                "[WARNING] Missing segments!",
                                missing_count,
                                "Sequence in live window.",
                            )
                            print(
                                "Expected next sequence:",
                                expected_next,
                            )
                            print(
                                "First available:",
                                first_new_sequence,
                            )
                            for sequence, segment in new_segments:
                                print_segment(
                                    sequence,
                                    segment,
                                    prefix= " [NEW]",
                                )
                previous_last_sequence = current_last_sequence
            except Exception as exc:
                current_time = datetime.now().strftime("%H:%M:%S")
                print(
                    f"\n[{current_time}]"
                    f"Reload #{reload_count}"
                )
                print(
                    f"[ERROR] {type(exc).__name__}: {exc}"
                )
            elapsed = time.monotonic() - request_started
            sleep_time = max(1.0, reload_interval - elapsed,)
            print (
                f"Reload next in "
                f"{sleep_time:.2f}s"
            )
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nStopped follow playlist")
if __name__ == "__main__":
    main()
