from dataclasses import dataclass, field
from typing import Any, Protocol

from core.context import (
    MonitoringContext,
    build_monitoring_context,
)


@dataclass
class CheckResult:
    name: str
    result: Any
    has_issue: bool = False


class MediaCheck(Protocol):
    name: str

    def run(
        self,
        context: MonitoringContext,
    ) -> CheckResult:
        ...


@dataclass
class MonitorReport:
    master_url: str
    context: MonitoringContext
    results: dict[str, CheckResult] = field(
        default_factory=dict
    )

    @property
    def has_issue(self) -> bool:
        return any(
            result.has_issue
            for result in self.results.values()
        )


class MediaMonitorService:

    def __init__(
        self,
        checks: list[MediaCheck],
    ):
        self.checks = checks

    def run(
        self,
        master_url: str,
        only: list[str] | None = None,
    ) -> MonitorReport:

        enabled_names = (
            set(only)
            if only is not None
            else None
        )

        checks = [
            check
            for check in self.checks
            if (
                enabled_names is None
                or check.name in enabled_names
            )
        ]

        context = build_monitoring_context(
            master_url
        )

        results = {}

        for check in checks:
            result = check.run(
                context
            )
            results[result.name] = result

        return MonitorReport(
            master_url=master_url,
            context=context,
            results=results,
        )
