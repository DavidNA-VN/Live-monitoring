import asyncio
import pytest

from presentation.api.adapters.base import CommandLookupState
from presentation.api.adapters.fakes import FakeAlertSource, FakeMonitoringControl
from presentation.api.models import (
    AlertCategory,
    AlertState,
    AudioLossCheck,
    BlackScreenCheck,
    CommandResultStatusEnum,
    CommandTypeEnum,
    EventType,
    StreamChecks,
    StreamConfigDTO,
    StreamStatusEnum,
)


@pytest.mark.anyio
async def test_fake_monitoring_control_lifecycle():
    control = FakeMonitoringControl()
    config = StreamConfigDTO(
        stream_id="test-stream-1",
        master_url="https://example.com/master.m3u8",
        checks=StreamChecks(
            black_screen=BlackScreenCheck(enabled=True),
            audio_loss=AudioLossCheck(
                enabled=False,
                threshold_dbfs=-30.0,
                duration_seconds=3.0,
            ),
        ),
    )

    # 1. Start stream
    submission = await control.start_stream(config)
    assert submission.status == "ACCEPTED"
    assert submission.stream_id == "test-stream-1"
    assert submission.command_id

    # Check status
    status = await control.get_status("test-stream-1")
    assert status is not None
    assert status.status == StreamStatusEnum.RUNNING
    assert status.checks["black_screen"] == "ENABLED"
    assert status.checks["audio_loss"] == "DISABLED"

    # Check command result
    cmd_lookup = await control.get_command_result(submission.command_id)
    assert cmd_lookup.state == CommandLookupState.FINAL
    assert cmd_lookup.result is not None
    assert cmd_lookup.result.status == CommandResultStatusEnum.APPLIED
    assert cmd_lookup.result.command_type == CommandTypeEnum.START
    assert cmd_lookup.result.changed is True

    duplicate = await control.start_stream(config)
    duplicate_lookup = await control.get_command_result(duplicate.command_id)
    assert duplicate_lookup.state == CommandLookupState.FINAL
    assert duplicate_lookup.result is not None
    assert duplicate_lookup.result.status == CommandResultStatusEnum.NOOP
    assert duplicate_lookup.result.changed is False

    # 2. Pause stream
    pause_sub = await control.pause_stream("test-stream-1")
    assert pause_sub.status == "ACCEPTED"
    status = await control.get_status("test-stream-1")
    assert status is not None
    assert status.status == StreamStatusEnum.PAUSED

    # 3. Resume stream
    resume_sub = await control.resume_stream("test-stream-1")
    assert resume_sub.status == "ACCEPTED"
    status = await control.get_status("test-stream-1")
    assert status is not None
    assert status.status == StreamStatusEnum.RUNNING

    # 4. Stop stream
    stop_sub = await control.stop_stream("test-stream-1")
    assert stop_sub.status == "ACCEPTED"
    status = await control.get_status("test-stream-1")
    assert status is None  # Stopped stream is removed from current active statuses

    # 5. Pause non-existent stream
    bad_pause_sub = await control.pause_stream("non-existent")
    assert bad_pause_sub.status == "ACCEPTED"
    bad_cmd_lookup = await control.get_command_result(bad_pause_sub.command_id)
    assert bad_cmd_lookup.state == CommandLookupState.FINAL
    assert bad_cmd_lookup.result is not None
    assert bad_cmd_lookup.result.status == CommandResultStatusEnum.REJECTED
    assert bad_cmd_lookup.result.error_code == "STREAM_NOT_FOUND"

    await control.close()


@pytest.mark.anyio
async def test_fake_control_rejects_update_for_unknown_stream():
    control = FakeMonitoringControl()
    config = StreamConfigDTO(
        stream_id="missing",
        master_url="https://example.com/master.m3u8",
        checks=StreamChecks(
            black_screen=BlackScreenCheck(enabled=True),
            audio_loss=AudioLossCheck(
                enabled=True,
                threshold_dbfs=-60.0,
                duration_seconds=30.0,
            ),
        ),
    )
    submission = await control.update_config(config)
    lookup = await control.get_command_result(submission.command_id)
    assert lookup.state == CommandLookupState.FINAL
    assert lookup.result is not None
    assert lookup.result.status == CommandResultStatusEnum.REJECTED
    assert lookup.result.error_code == "STREAM_NOT_FOUND"
    assert await control.get_status("missing") is None


@pytest.mark.anyio
async def test_fake_alert_source_lifecycle_and_cleanup():
    alert_source = FakeAlertSource()
    stream_id = "test-stream-alerts"

    # Start generation with very fast cycle for testing
    alert_source.start_generating_fake_alerts(
        stream_id,
        interval_seconds=0.05,
        recovery_seconds=0.05,
    )

    received_alerts = []

    async def consume_alerts():
        async for alert in alert_source.subscribe(stream_id):
            received_alerts.append(alert)
            if len(received_alerts) >= 2:
                break

    await asyncio.wait_for(consume_alerts(), timeout=2.0)

    assert len(received_alerts) >= 2
    assert received_alerts[0].state == AlertState.OPEN
    assert received_alerts[1].state == AlertState.RESOLVED
    assert received_alerts[1].event_id == received_alerts[0].event_id
    assert received_alerts[1].event_started_at == received_alerts[0].event_started_at
    assert received_alerts[1].event_ended_at is not None

    recent = await alert_source.recent(stream_id, limit=10)
    assert len(recent) >= 2

    # Test stop stream
    await alert_source.stop_stream(stream_id)
    assert stream_id not in alert_source._stream_tasks

    # Test close
    await alert_source.close()
    assert len(alert_source._stream_tasks) == 0
    assert len(alert_source._subscribers) == 0


@pytest.mark.anyio
async def test_fake_alert_history_is_bounded():
    alert_source = FakeAlertSource(max_recent_per_stream=2)
    for _ in range(3):
        await alert_source.publish_alert(
            alert_source._create_alert("bounded", AlertState.OPEN)
        )
    assert len(await alert_source.recent("bounded", limit=10)) == 2
