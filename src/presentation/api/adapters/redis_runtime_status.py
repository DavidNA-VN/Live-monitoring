import logging
from typing import Any, Optional

import redis.exceptions

from core.redis_keys import PublicRuntimeRedisKeys
from .base import RuntimeStatusReader
from .errors import PresentationServiceUnavailableError
from .runtime_status_codec import (
    RuntimeStatusCodecError,
    parse_public_runtime_status,
)
from ..models import RuntimeStatusDTO

logger = logging.getLogger(__name__)


class RuntimeStatusReaderError(Exception):
    """Base exception cho RedisRuntimeStatusReader."""
    pass


class RuntimeStatusRedisUnavailableError(
    RuntimeStatusReaderError,
    PresentationServiceUnavailableError,
):
    """Lỗi khi Redis hash không khả dụng hoặc kết nối timeout."""
    pass


class CorruptRuntimeStatusError(
    RuntimeStatusReaderError,
    PresentationServiceUnavailableError,
):
    """Lỗi khi dữ liệu snapshot trong Redis hash bị hỏng hoặc sai contract."""
    pass


class RedisRuntimeStatusReader(RuntimeStatusReader):
    """
    Adapter đọc trạng thái runtime của Stream từ Redis Hash media-monitor:v1:public:runtime-status.
    Thực hiện duy nhất 1 lệnh HGET theo external_stream_id và parse strict DTO.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: Optional[PublicRuntimeRedisKeys] = None,
    ) -> None:
        self.redis = redis_client
        self.keys = keys or PublicRuntimeRedisKeys()

    async def get_status(
        self,
        stream_id: str,
    ) -> Optional[RuntimeStatusDTO]:
        if not stream_id or not stream_id.strip():
            raise ValueError("stream_id must not be empty")

        hash_key = self.keys.current_statuses()

        try:
            raw_payload = await self.redis.hget(hash_key, stream_id)
        except redis.exceptions.RedisError as exc:
            logger.error(
                "Redis HGET failed on %s for stream_id=%s: %s",
                hash_key,
                stream_id,
                type(exc).__name__,
            )
            raise RuntimeStatusRedisUnavailableError(f"Redis unavailable: {exc}") from exc
        if raw_payload is None:
            return None

        try:
            return parse_public_runtime_status(
                raw_payload,
                requested_stream_id=stream_id,
            )
        except RuntimeStatusCodecError as exc:
            logger.error(
                "Corrupt runtime status payload in Redis for stream_id=%s: %s",
                stream_id,
                exc,
            )
            raise CorruptRuntimeStatusError(
                f"Corrupt runtime status payload for stream '{stream_id}': {exc}"
            ) from exc

    async def close(self) -> None:
        # Externally owned client; lifecycle managed by composition root
        pass
