from pathlib import Path
import argparse


TS_PACKET_SIZE = 188
SYNC_BYTE = 0x47


def parse_pid(value: str) -> int:
    return int(value, 0)


def corrupt_segment(
    path: Path,
    video_pid: int,
) -> None:
    data = bytearray(
        path.read_bytes()
    )

    if len(data) % TS_PACKET_SIZE != 0:
        print(
            "Warning: file size is not an exact "
            "multiple of 188 bytes."
        )

    packet_count = (
        len(data)
        // TS_PACKET_SIZE
    )

    candidates = []

    for index in range(packet_count):
        offset = index * TS_PACKET_SIZE

        packet = data[
            offset:
            offset + TS_PACKET_SIZE
        ]

        if len(packet) < TS_PACKET_SIZE:
            continue

        if packet[0] != SYNC_BYTE:
            continue

        pid = (
            ((packet[1] & 0x1F) << 8)
            | packet[2]
        )

        if pid == video_pid:
            candidates.append(index)

    if len(candidates) < 10:
        raise RuntimeError(
            f"Too few packets found for PID "
            f"0x{video_pid:X}"
        )

    # Chọn packet gần giữa stream video,
    # tránh packet đầu/cuối segment.
    target_index = candidates[
        len(candidates) // 2
    ]

    offset = (
        target_index
        * TS_PACKET_SIZE
    )

    print(
        f"Target TS packet : {target_index}"
    )
    print(
        f"Video PID        : 0x{video_pid:X}"
    )

    del data[
        offset:
        offset + TS_PACKET_SIZE
    ]

    path.write_bytes(
        data
    )

    print(
        f"Corrupted segment: {path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "segment",
    )

    parser.add_argument(
        "--pid",
        required=True,
        help=(
            "Video PID, e.g. 0x100"
        ),
    )

    args = parser.parse_args()

    corrupt_segment(
        Path(args.segment),
        parse_pid(args.pid),
    )


if __name__ == "__main__":
    main()