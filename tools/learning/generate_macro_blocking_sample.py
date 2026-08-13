from generate_hls_samples import (
    BASE_DIR,
    clean_and_create_dir,
    generate_hls,
    get_macro_blocking_input_file,
)


def main() -> int:
    macro_blocking_input_file = get_macro_blocking_input_file()

    if macro_blocking_input_file is None:
        print("Loi: Khong tim thay input video macroblocking:")
        print(BASE_DIR.parent / "test_assets" / "input_macroblocking.mp4")
        return 1

    output_dir = BASE_DIR / "sample_05_macro_blocking"
    clean_and_create_dir(output_dir)

    print("Generating Sample 05: Macro Blocking...")

    if not generate_hls(
        output_dir=output_dir,
        input_file=macro_blocking_input_file,
    ):
        return 1

    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
