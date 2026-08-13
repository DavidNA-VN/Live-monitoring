import argparse

from core.monitor import MediaMonitorService
from core.registry import default_checks, get_check_names
from reporting.console import print_monitor_report


def parse_args() -> argparse.Namespace:

    checks = default_checks()

    parser = argparse.ArgumentParser(
        description=(
            "Run media-monitor checks "
            "for an HLS master playlist."
        )
    )

    parser.add_argument(
        "--url",
        required=True,
        help="HLS master playlist URL.",
    )

    parser.add_argument(
        "--only",
        choices=get_check_names(checks),
        action="append",
        help=(
            "Debug filter. Can be passed multiple times. "
            "Production should omit this to run all checks."
        ),
    )

    parser.add_argument(
        "--case",
        choices=get_check_names(checks),
        help=(
            "Deprecated alias for --only, kept for "
            "development compatibility."
        ),
    )

    return parser.parse_args()


def main() -> int:

    args = parse_args()
    checks = default_checks()

    only = args.only

    if args.case:
        only = [
            args.case
        ]

    service = MediaMonitorService(
        checks=checks
    )

    report = service.run(
        master_url=args.url,
        only=only,
    )

    print_monitor_report(
        report
    )

    return 1 if report.has_issue else 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
