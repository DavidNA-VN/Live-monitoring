from unittest.mock import AsyncMock
import pytest

from presentation.api.adapters.fakes import FakeAlertSource, FakeMonitoringControl
from presentation.api.adapters.redis_alert_source import RedisAlertSource
from presentation.api.adapters.redis_command_result_reader import RedisCommandResultReader
from presentation.api.adapters.redis_monitoring_control import RedisMonitoringControl
from presentation.api.adapters.redis_runtime_status import RedisRuntimeStatusReader
from presentation.api.composition import (
    PresentationDependencies,
    build_dependencies,
)
from presentation.api.settings import ApiSettings


@pytest.mark.anyio
async def test_composition_fake_mode():
    settings = ApiSettings(mode="fake", enable_fake_generator=True)
    deps = await build_dependencies(settings)

    assert deps.mode == "fake"
    assert deps.redis_client is None
    assert deps.enable_fake_generator is True
    assert isinstance(deps.control, FakeMonitoringControl)
    assert deps.command_results is deps.control
    assert deps.status_reader is deps.control
    assert isinstance(deps.alert_source, FakeAlertSource)

    # Closing does not fail
    await deps.close()


@pytest.mark.anyio
async def test_composition_redis_mode_wires_shared_client():
    settings = ApiSettings(
        mode="redis",
        redis_url="redis://localhost:6379/15",
        redis_prefix="test-monitor:v1",
        alert_history_scan_limit=500,
        websocket_redis_block_ms=250,
    )
    deps = await build_dependencies(settings)

    assert deps.mode == "redis"
    assert deps.redis_client is not None
    assert deps.enable_fake_generator is False

    assert isinstance(deps.control, RedisMonitoringControl)
    assert isinstance(deps.command_results, RedisCommandResultReader)
    assert isinstance(deps.status_reader, RedisRuntimeStatusReader)
    assert isinstance(deps.alert_source, RedisAlertSource)

    # All adapters share the exact same Redis client instance
    assert deps.control.redis is deps.redis_client
    assert deps.command_results.redis is deps.redis_client
    assert deps.status_reader.redis is deps.redis_client
    assert deps.alert_source.redis is deps.redis_client

    # Verify prefix is applied
    assert deps.control.keys.prefix == "test-monitor:v1"
    assert deps.status_reader.keys.prefix == "test-monitor:v1"
    assert deps.alert_source.keys.prefix == "test-monitor:v1"
    assert deps.alert_source.history_scan_limit == 500
    assert deps.alert_source.xread_block_milliseconds == 250

    await deps.close()


@pytest.mark.anyio
async def test_composition_close_order_and_idempotency():
    mock_redis = AsyncMock()
    mock_alert_source = AsyncMock()
    mock_control = AsyncMock()
    mock_cmd_reader = AsyncMock()
    mock_status_reader = AsyncMock()

    call_order = []

    mock_alert_source.close.side_effect = lambda: call_order.append("alert_source")
    mock_control.close.side_effect = lambda: call_order.append("control")
    mock_cmd_reader.close.side_effect = lambda: call_order.append("cmd_reader")
    mock_status_reader.close.side_effect = lambda: call_order.append("status_reader")
    mock_redis.aclose.side_effect = lambda: call_order.append("redis_client")

    deps = PresentationDependencies(
        control=mock_control,
        command_results=mock_cmd_reader,
        status_reader=mock_status_reader,
        alert_source=mock_alert_source,
        redis_client=mock_redis,
        mode="redis",
    )

    await deps.close()
    await deps.close()

    assert call_order[0] == "alert_source"
    assert "redis_client" == call_order[-1]
    assert mock_redis.aclose.call_count == 1
    assert mock_alert_source.close.call_count == 1
