from dataclasses import dataclass, field
import logging
from typing import Any, Literal, Optional

import redis.asyncio as aioredis

from core.redis_keys import AlertRedisKeys, ControlRedisKeys, PublicRuntimeRedisKeys, RedisNamespace
from .adapters.base import (
    AlertSource,
    CommandResultReader,
    MonitoringControl,
    RuntimeStatusReader,
)
from .adapters.fakes import FakeAlertSource, FakeMonitoringControl
from .adapters.redis_alert_source import RedisAlertSource
from .adapters.redis_command_result_reader import RedisCommandResultReader
from .adapters.redis_monitoring_control import RedisMonitoringControl
from .adapters.redis_runtime_status import RedisRuntimeStatusReader
from .settings import ApiSettings

logger = logging.getLogger(__name__)


@dataclass
class PresentationDependencies:
    """Gói dependencies đã được khởi tạo và cấu hình cho Presentation API."""

    control: MonitoringControl
    command_results: CommandResultReader
    status_reader: RuntimeStatusReader
    alert_source: AlertSource
    redis_client: Optional[Any] = None
    mode: Literal["fake", "redis"] = "fake"
    enable_fake_generator: bool = False
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        """
        Dọn dẹp sạch sẽ tài nguyên theo thứ tự:
        1. Đóng alert_source trước để hủy các subscription / blocking XREAD tasks.
        2. Đóng các adapters khác (tránh đóng trùng instance).
        3. Đóng shared Redis client cuối cùng (One-Owner Rule).
        """
        if self._closed:
            return
        self._closed = True
        closed_ids: set[int] = set()

        # 1. Đóng alert_source trước
        if self.alert_source is not None:
            closed_ids.add(id(self.alert_source))
            try:
                await self.alert_source.close()
            except Exception as exc:
                logger.warning(
                    "Error closing %s during shutdown: %s",
                    type(self.alert_source).__name__,
                    type(exc).__name__,
                )

        # 2. Đóng các adapter còn lại
        other_adapters = (self.control, self.command_results, self.status_reader)
        for adapter in other_adapters:
            if adapter is not None and id(adapter) not in closed_ids:
                closed_ids.add(id(adapter))
                try:
                    await adapter.close()
                except Exception as exc:
                    logger.warning(
                        "Error closing %s during shutdown: %s",
                        type(adapter).__name__,
                        type(exc).__name__,
                    )

        # 3. Đóng shared Redis client cuối cùng
        if self.redis_client is not None:
            try:
                await self.redis_client.aclose()
            except Exception as exc:
                logger.warning(
                    "Error closing Redis client during shutdown: %s",
                    type(exc).__name__,
                )


async def build_dependencies(settings: ApiSettings) -> PresentationDependencies:
    """
    Composition Root: Khởi tạo toàn bộ dependencies tương ứng theo cấu hình ApiSettings.
    """
    if settings.mode == "fake":
        fake_control = FakeMonitoringControl()
        fake_alert = FakeAlertSource()
        return PresentationDependencies(
            control=fake_control,
            command_results=fake_control,
            status_reader=fake_control,
            alert_source=fake_alert,
            redis_client=None,
            mode="fake",
            enable_fake_generator=settings.enable_fake_generator,
        )

    if settings.mode == "redis":
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
        namespace = RedisNamespace(settings.redis_prefix)
        control_keys = ControlRedisKeys(namespace)
        public_keys = PublicRuntimeRedisKeys(namespace)
        alert_keys = AlertRedisKeys(namespace)

        control = RedisMonitoringControl(
            redis_client=redis_client,
            keys=control_keys,
        )
        cmd_reader = RedisCommandResultReader(
            redis_client=redis_client,
            keys=control_keys,
        )
        status_reader = RedisRuntimeStatusReader(
            redis_client=redis_client,
            keys=public_keys,
        )
        alert_source = RedisAlertSource(
            redis_client=redis_client,
            keys=alert_keys,
            history_scan_limit=settings.alert_history_scan_limit,
            xread_block_milliseconds=settings.websocket_redis_block_ms,
        )

        return PresentationDependencies(
            control=control,
            command_results=cmd_reader,
            status_reader=status_reader,
            alert_source=alert_source,
            redis_client=redis_client,
            mode="redis",
            enable_fake_generator=False,
        )

    raise ValueError(f"Unsupported mode '{settings.mode}'")
