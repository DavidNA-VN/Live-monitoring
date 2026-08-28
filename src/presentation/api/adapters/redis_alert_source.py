import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import logging
from typing import Any, List, Optional, Set

import redis.exceptions

from core.redis_keys import AlertRedisKeys
from .alert_codec import AlertCodecError, parse_alert_fields
from .base import AlertSource
from .errors import PresentationAdapterError, PresentationServiceUnavailableError
from ..models import AlertDTO

logger = logging.getLogger(__name__)


class AlertSourceError(PresentationAdapterError):
    """Base exception cho AlertSource adapters."""
    pass


class AlertRedisUnavailableError(AlertSourceError, PresentationServiceUnavailableError):
    """Lỗi khi Redis alert outbox không khả dụng hoặc kết nối timeout."""
    pass


class RedisAlertSource(AlertSource):
    """
    Adapter đọc Alert History và WebSocket Realtime từ Redis Stream media-monitor:v1:alerts:outbox.
    Sử dụng bounded paginated XREVRANGE cho REST history và independent XREAD cursor cho WebSocket fan-out.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: Optional[AlertRedisKeys] = None,
        history_scan_limit: int = 1000,
        history_page_size: int = 100,
        xread_block_milliseconds: int = 1000,
        xread_count: int = 100,
        reconnect_initial_seconds: float = 0.1,
        reconnect_max_seconds: float = 2.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if history_scan_limit <= 0:
            raise ValueError("history_scan_limit must be > 0")
        if history_page_size <= 0:
            raise ValueError("history_page_size must be > 0")
        if xread_block_milliseconds <= 0:
            raise ValueError("xread_block_milliseconds must be > 0")
        if xread_count <= 0:
            raise ValueError("xread_count must be > 0")
        if reconnect_initial_seconds <= 0:
            raise ValueError("reconnect_initial_seconds must be > 0")
        if reconnect_max_seconds <= 0:
            raise ValueError("reconnect_max_seconds must be > 0")
        if reconnect_initial_seconds > reconnect_max_seconds:
            raise ValueError("reconnect_initial_seconds must be <= reconnect_max_seconds")

        self.redis = redis_client
        self.keys = keys or AlertRedisKeys()
        self.history_scan_limit = history_scan_limit
        self.history_page_size = history_page_size
        self.xread_block_milliseconds = xread_block_milliseconds
        self.xread_count = xread_count
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.sleep_fn = sleep_fn
        self._closed = False
        self._subscription_tasks: Set[asyncio.Task[Any]] = set()

    async def recent(
        self,
        stream_id: str,
        limit: int = 50,
    ) -> List[AlertDTO]:
        if not stream_id or not stream_id.strip():
            raise ValueError("stream_id must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        matched: List[AlertDTO] = []
        scanned = 0
        cursor = "+"
        outbox_key = self.keys.outbox()

        while len(matched) < limit and scanned < self.history_scan_limit:
            page_count = min(self.history_page_size, self.history_scan_limit - scanned)
            try:
                entries = await self.redis.xrevrange(
                    outbox_key,
                    max=cursor,
                    min="-",
                    count=page_count,
                )
            except redis.exceptions.RedisError as exc:
                logger.error(
                    "Redis XREVRANGE failed on %s: %s",
                    outbox_key,
                    type(exc).__name__,
                )
                raise AlertRedisUnavailableError(f"Redis unavailable: {exc}") from exc

            if not entries:
                break

            for entry_tuple in entries:
                scanned += 1
                entry_id, fields = entry_tuple[0], entry_tuple[1]
                entry_id_str = entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)

                try:
                    alert = parse_alert_fields(fields)
                    if alert.stream_id == stream_id:
                        matched.append(alert)
                        if len(matched) == limit:
                            break
                except AlertCodecError as exc:
                    logger.warning(
                        "Skipping poison alert entry %s in outbox for stream %s: %s",
                        entry_id_str,
                        stream_id,
                        type(exc).__name__,
                    )

            last_entry_id = entries[-1][0]
            last_id_str = last_entry_id.decode("utf-8") if isinstance(last_entry_id, bytes) else str(last_entry_id)
            cursor = f"({last_id_str}"

        # Trả về theo thứ tự thời gian (chronological: cũ -> mới)
        matched.reverse()
        return matched

    async def subscribe(
        self,
        stream_id: str,
    ) -> AsyncIterator[AlertDTO]:
        if not stream_id or not stream_id.strip():
            raise ValueError("stream_id must not be empty")

        outbox_key = self.keys.outbox()
        backoff_delay = self.reconnect_initial_seconds
        current_task = asyncio.current_task()
        if current_task is not None:
            self._subscription_tasks.add(current_task)

        try:
            cursor = await self._initial_cursor(outbox_key, stream_id)
            while not self._closed:
                try:
                    response = await self.redis.xread(
                        {outbox_key: cursor},
                        count=self.xread_count,
                        block=self.xread_block_milliseconds,
                    )
                    if self._closed:
                        break

                    backoff_delay = self.reconnect_initial_seconds

                    if response:
                        for _, entries in response:
                            for entry_tuple in entries:
                                entry_id, fields = entry_tuple[0], entry_tuple[1]
                                entry_id_str = entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
                                cursor = entry_id_str

                                try:
                                    alert = parse_alert_fields(fields)
                                    if alert.stream_id == stream_id:
                                        yield alert
                                except AlertCodecError as exc:
                                    logger.warning(
                                        "Skipping poison alert entry %s in WebSocket subscription for stream %s: %s",
                                        entry_id_str,
                                        stream_id,
                                        type(exc).__name__,
                                    )
                except redis.exceptions.RedisError as exc:
                    if self._closed:
                        break
                    logger.warning(
                        "Redis error in alert subscription for stream %s, retrying in %.2fs: %s",
                        stream_id,
                        backoff_delay,
                        type(exc).__name__,
                    )
                    await self.sleep_fn(backoff_delay)
                    backoff_delay = min(backoff_delay * 2.0, self.reconnect_max_seconds)
        finally:
            if current_task is not None:
                self._subscription_tasks.discard(current_task)

    async def _initial_cursor(self, outbox_key: str, stream_id: str) -> str:
        """Resolve `$` once so later XREAD timeouts cannot create delivery gaps."""
        backoff_delay = self.reconnect_initial_seconds
        while not self._closed:
            try:
                entries = await self.redis.xrevrange(
                    outbox_key,
                    max="+",
                    min="-",
                    count=1,
                )
                if not entries:
                    return "0-0"
                entry_id = entries[0][0]
                return entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
            except redis.exceptions.RedisError as exc:
                logger.warning(
                    "Redis error while opening alert subscription for stream %s, retrying in %.2fs: %s",
                    stream_id,
                    backoff_delay,
                    type(exc).__name__,
                )
                await self.sleep_fn(backoff_delay)
                backoff_delay = min(backoff_delay * 2.0, self.reconnect_max_seconds)
        return "0-0"

    async def stop_stream(self, stream_id: str) -> None:
        pass

    async def close(self) -> None:
        self._closed = True
        current_task = asyncio.current_task()
        tasks = [task for task in self._subscription_tasks if task is not current_task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
