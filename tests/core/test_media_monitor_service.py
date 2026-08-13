from dataclasses import dataclass

import core.monitor as monitor
from core.context import MonitoringContext
from core.monitor import CheckResult, MediaMonitorService


@dataclass
class FakeCheck:
    name: str
    has_issue: bool = False

    def run(
        self,
        context: MonitoringContext,
    ) -> CheckResult:

        return CheckResult(
            name=self.name,
            result={
                "master_url": context.master_url,
            },
            has_issue=self.has_issue,
        )


def make_context(
    master_url: str,
) -> MonitoringContext:

    return MonitoringContext(
        master_url=master_url,
        variants=[],
        segments_by_variant={},
    )


def test_service_runs_multiple_checks(monkeypatch):
    calls = []

    def fake_builder(master_url: str):
        calls.append(master_url)
        return make_context(master_url)

    monkeypatch.setattr(
        monitor,
        "build_monitoring_context",
        fake_builder,
    )

    service = MediaMonitorService(
        checks=[
            FakeCheck("black_screen"),
            FakeCheck("freeze_frame"),
        ]
    )

    report = service.run(
        "http://example.test/master.m3u8"
    )

    assert calls == [
        "http://example.test/master.m3u8"
    ]
    assert set(report.results) == {
        "black_screen",
        "freeze_frame",
    }


def test_service_only_filter(monkeypatch):
    monkeypatch.setattr(
        monitor,
        "build_monitoring_context",
        make_context,
    )

    service = MediaMonitorService(
        checks=[
            FakeCheck("black_screen"),
            FakeCheck("freeze_frame"),
        ]
    )

    report = service.run(
        "http://example.test/master.m3u8",
        only=[
            "black_screen",
        ],
    )

    assert set(report.results) == {
        "black_screen",
    }


def test_service_builds_context_once(monkeypatch):
    call_count = 0

    def fake_builder(master_url: str):
        nonlocal call_count
        call_count += 1
        return make_context(master_url)

    monkeypatch.setattr(
        monitor,
        "build_monitoring_context",
        fake_builder,
    )

    service = MediaMonitorService(
        checks=[
            FakeCheck("black_screen"),
            FakeCheck("freeze_frame"),
        ]
    )

    service.run(
        "http://example.test/master.m3u8"
    )

    assert call_count == 1


def test_report_can_contain_multiple_issue_types(monkeypatch):
    monkeypatch.setattr(
        monitor,
        "build_monitoring_context",
        make_context,
    )

    service = MediaMonitorService(
        checks=[
            FakeCheck(
                "black_screen",
                has_issue=True,
            ),
            FakeCheck(
                "freeze_frame",
                has_issue=True,
            ),
        ]
    )

    report = service.run(
        "http://example.test/master.m3u8"
    )

    assert report.has_issue
    assert report.results["black_screen"].has_issue
    assert report.results["freeze_frame"].has_issue
