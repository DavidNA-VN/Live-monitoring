import shutil
import subprocess
import sys
from pathlib import Path


# Script nằm trong:
# media-monitor/src/generate_hls_samples.py
def find_project_root() -> Path:
    current_path = Path(__file__).resolve()

    for parent in current_path.parents:
        if (
            (parent / "source_videos").is_dir()
            and (parent / "test_assets").is_dir()
        ):
            return parent

    raise RuntimeError(
        "Cannot find project root from script location."
    )


PROJECT_ROOT = find_project_root()

INPUT_FILE = PROJECT_ROOT / "test_assets" / "input.mp4"
MACRO_BLOCKING_INPUT_FILE = PROJECT_ROOT / "test_assets" / "input_macroblocking.mp4"
MACRO_BLOCKING_INPUT_FALLBACK_FILE = PROJECT_ROOT / "test_assets" / "input-macroblocking.mp4"
MACRO_BLOCKING_TEST2_INPUT_FILE = PROJECT_ROOT / "test_assets" / "input_macroblocking-test2.mp4"
BASE_DIR = PROJECT_ROOT / "hls_samples"

HLS_TIME = 3

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_cmd(cmd: list[str], cwd: Path | None = None) -> bool:
    """
    Chạy command không qua shell để tránh lỗi quote và path trên Windows.
    """

    print(f"[RUNNING]: {subprocess.list2cmdline(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )

    if result.returncode != 0:
        print("[ERROR]")
        print(result.stderr)
        return False

    return True


def clean_and_create_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return

    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def generate_hls(
    output_dir: Path,
    input_file: Path = INPUT_FILE,
    video_filter: str | None = None,
) -> bool:
    """
    Tạo một HLS VOD với segment được đặt tên rõ ràng:
    segment_000.ts
    segment_001.ts
    ...
    """

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(input_file),

        # Lấy video đầu tiên và audio đầu tiên nếu có
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
    ]

    if video_filter:
        cmd.extend(["-vf", video_filter])

    cmd.extend([
        "-c:v",
        "libx264",

        "-pix_fmt",
        "yuv420p",

        # Ép keyframe tại 0, 3, 6, 9... giây
        "-force_key_frames",
        f"expr:gte(t,n_forced*{HLS_TIME})",

        # Không tự chèn thêm keyframe do scene change
        "-sc_threshold",
        "0",

        "-c:a",
        "aac",

        "-f",
        "hls",

        "-hls_time",
        str(HLS_TIME),

        "-hls_playlist_type",
        "vod",

        # Các segment đều bắt đầu bằng keyframe
        "-hls_flags",
        "independent_segments",

        "-hls_segment_filename",
        "segment_%03d.ts",

        "stream.m3u8",
    ])

    return run_cmd(cmd, cwd=output_dir)


def get_macro_blocking_input_file() -> Path | None:
    if MACRO_BLOCKING_INPUT_FILE.exists():
        return MACRO_BLOCKING_INPUT_FILE

    if MACRO_BLOCKING_INPUT_FALLBACK_FILE.exists():
        print(
            "[WARNING]: Khong tim thay input_macroblocking.mp4, "
            "dang dung input-macroblocking.mp4."
        )
        return MACRO_BLOCKING_INPUT_FALLBACK_FILE

    return None


def get_macro_blocking_test2_input_file() -> Path | None:
    if MACRO_BLOCKING_TEST2_INPUT_FILE.exists():
        return MACRO_BLOCKING_TEST2_INPUT_FILE

    return None


def corrupt_ts_packets(
    file_path: Path,
    start_ratio: float = 0.3,
    packet_count: int = 20,
) -> None:
    """
    MPEG-TS thường dùng packet 188 byte.

    Hàm này ghi đè nhiều packet liên tiếp bằng zero để tạo lỗi
    transport-stream rõ ràng và có tính lặp lại.
    """

    ts_packet_size = 188

    if not file_path.exists():
        print(f"[WARNING]: Không tìm thấy segment cần corrupt: {file_path.name}")
        return

    total_size = file_path.stat().st_size
    total_packets = total_size // ts_packet_size

    if total_packets == 0:
        print(f"[WARNING]: File quá nhỏ để corrupt: {file_path.name}")
        return

    start_packet = int(total_packets * start_ratio)

    available_packets = total_packets - start_packet
    actual_packet_count = min(packet_count, available_packets)

    if actual_packet_count <= 0:
        print(f"[WARNING]: Không còn packet để corrupt: {file_path.name}")
        return

    byte_position = start_packet * ts_packet_size
    corrupt_size = actual_packet_count * ts_packet_size

    with file_path.open("r+b") as file:
        file.seek(byte_position)
        file.write(b"\x00" * corrupt_size)

    print(
        f"  └─> [CORRUPTED]: Ghi đè {actual_packet_count} TS packet "
        f"({corrupt_size} bytes) trong {file_path.name}"
    )


def main() -> None:
    if shutil.which("ffmpeg") is None:
        print("Lỗi: Không tìm thấy FFmpeg trong PATH.")
        return

    if not INPUT_FILE.exists():
        print(f"Lỗi: Không tìm thấy input video:")
        print(INPUT_FILE)
        print("\nHãy đặt file tại:")
        print(PROJECT_ROOT / "test_assets" / "input.mp4")
        return

    macro_blocking_input_file = get_macro_blocking_input_file()
    macro_blocking_test2_input_file = get_macro_blocking_test2_input_file()

    if macro_blocking_input_file is None:
        print("Loi: Khong tim thay input video macroblocking:")
        print(MACRO_BLOCKING_INPUT_FILE)
        print("\nHay dat file tai:")
        print(PROJECT_ROOT / "test_assets" / "input_macroblocking.mp4")
        return

    if macro_blocking_test2_input_file is None:
        print("Loi: Khong tim thay input video macroblocking test 2:")
        print(MACRO_BLOCKING_TEST2_INPUT_FILE)
        print("\nHay dat file tai:")
        print(PROJECT_ROOT / "test_assets" / "input_macroblocking-test2.mp4")
        return

    clean_and_create_dir(BASE_DIR)

    print("=== BẮT ĐẦU TẠO CÁC SAMPLE TEST HLS ===\n")

    # -------------------------------------------------------------
    # SAMPLE 1: CLEAN
    # -------------------------------------------------------------
    dir_1 = BASE_DIR / "sample_01_clean"
    clean_and_create_dir(dir_1)

    print("[1/6] Generating Sample 01: Clean Baseline...")
    generate_hls(dir_1)

    # -------------------------------------------------------------
    # SAMPLE 2: MISSING SEGMENT
    # -------------------------------------------------------------
    dir_2 = BASE_DIR / "sample_02_missing_segment"
    clean_and_create_dir(dir_2)

    print("\n[2/6] Generating Sample 02: Missing Segment...")
    if generate_hls(dir_2):
        segment_to_remove = dir_2 / "segment_002.ts"

        if segment_to_remove.exists():
            segment_to_remove.unlink()

            print(
                f"  └─> [DELETED]: Removed {segment_to_remove.name} "
                "to simulate missing segment."
            )
        else:
            print(
                "  └─> [WARNING]: Không có segment_002.ts. "
                "Input video có thể quá ngắn."
            )

    # -------------------------------------------------------------
    # SAMPLE 3: BLACK SCREEN
    # -------------------------------------------------------------
    dir_3 = BASE_DIR / "sample_03_black_screen"
    clean_and_create_dir(dir_3)

    print("\n[3/6] Generating Sample 03: Black Screen...")

    black_filter = (
        "drawbox="
        "x=0:"
        "y=0:"
        "w=iw:"
        "h=ih:"
        "color=black@1:"
        "t=fill:"
        "enable='between(t,3,6)'"
    )

    generate_hls(
        output_dir=dir_3,
        video_filter=black_filter,
    )

    # -------------------------------------------------------------
    # SAMPLE 4: MPEG-TS CORRUPTION
    # -------------------------------------------------------------
    dir_4 = BASE_DIR / "sample_04_ts_corruption"
    clean_and_create_dir(dir_4)

    print("\n[4/6] Generating Sample 04: MPEG-TS Corruption...")

    if generate_hls(dir_4):
        corrupt_ts_packets(
            file_path=dir_4 / "segment_001.ts",
            start_ratio=0.3,
            packet_count=20,
        )

    # -------------------------------------------------------------
    # SAMPLE 5: MACRO BLOCKING
    # -------------------------------------------------------------
    dir_5 = BASE_DIR / "sample_05_macro_blocking"
    clean_and_create_dir(dir_5)

    print("\n[5/6] Generating Sample 05: Macro Blocking...")
    generate_hls(
        output_dir=dir_5,
        input_file=macro_blocking_input_file,
    )

    # -------------------------------------------------------------
    # SAMPLE 6: MACRO BLOCKING TEST 2
    # -------------------------------------------------------------
    dir_6 = BASE_DIR / "sample_06_macro_blocking"
    clean_and_create_dir(dir_6)

    print("\n[6/6] Generating Sample 06: Macro Blocking Test 2...")
    generate_hls(
        output_dir=dir_6,
        input_file=macro_blocking_test2_input_file,
    )

    print("\n=======================================================")
    print("HOÀN THÀNH")
    print(f"Output: {BASE_DIR}")
    print("=======================================================")


if __name__ == "__main__":
    main()
