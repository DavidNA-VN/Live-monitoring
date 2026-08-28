import json
import logging
from typing import Any, Optional

import redis.exceptions

from core.redis_keys import ControlRedisKeys
from .base import CommandLookup, CommandLookupState, CommandResultReader
from .errors import PresentationServiceUnavailableError
from .redis_command_codec import (
    CommandCodecError,
    parse_command_result,
    parse_submission_record,
)

logger = logging.getLogger(__name__)


class CommandResultReaderError(Exception):
    """Base exception cho RedisCommandResultReader."""
    pass


class ReaderRedisUnavailableError(
    CommandResultReaderError,
    PresentationServiceUnavailableError,
):
    """Lỗi khi Redis không khả dụng hoặc timeout."""
    pass


class CorruptMarkerError(
    CommandResultReaderError,
    PresentationServiceUnavailableError,
):
    """Lỗi khi dữ liệu marker trong Redis bị corrupt/sai cấu trúc."""
    pass


class RedisCommandResultReader(CommandResultReader):
    """
    Adapter tra cứu kết quả xử lý command từ Redis với độ phức tạp O(1).
    Phân biệt chính xác giữa PENDING (đã nhận), FINAL (đã xử lý), và MISSING (không tồn tại).
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: Optional[ControlRedisKeys] = None,
    ) -> None:
        self.redis = redis_client
        self.keys = keys or ControlRedisKeys()

    def _to_text(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def get_command_result(self, command_id: str) -> CommandLookup:
        if not command_id or not command_id.strip():
            raise ValueError("command_id must not be empty")

        processed_key = self.keys.processed_command(command_id)
        submitted_key = self.keys.submitted_command(command_id)

        try:
            # 1. Kiểm tra processed marker trước (O(1))
            processed_raw = await self.redis.get(processed_key)
            if processed_raw is not None:
                try:
                    marker_text = self._to_text(processed_raw)
                    marker_dict = json.loads(marker_text)
                    if not isinstance(marker_dict, dict):
                        raise CorruptMarkerError("Processed marker must be a JSON object")
                    result_data = marker_dict.get("result")
                    if not isinstance(result_data, dict):
                        raise CorruptMarkerError("Missing 'result' object in processed marker")
                    result_dto = parse_command_result(result_data)
                    if result_dto.command_id != command_id:
                        raise CorruptMarkerError(
                            "Processed marker command_id does not match lookup key"
                        )
                    return CommandLookup(
                        state=CommandLookupState.FINAL,
                        result=result_dto,
                    )
                except CommandCodecError as exc:
                    logger.error("Corrupt command result payload for command %s: %s", command_id, exc)
                    raise CorruptMarkerError(f"Corrupt command result payload: {exc}") from exc
                except json.JSONDecodeError as exc:
                    logger.error("Corrupt JSON in processed marker for command %s: %s", command_id, exc)
                    raise CorruptMarkerError(f"Corrupt JSON in processed marker: {exc}") from exc

            # 2. Nếu chưa processed, kiểm tra submitted marker (O(1))
            submitted_raw = await self.redis.get(submitted_key)
            if submitted_raw is not None:
                try:
                    sub_text = self._to_text(submitted_raw)
                    sub_dict = json.loads(sub_text)
                    if not isinstance(sub_dict, dict):
                        raise CorruptMarkerError("Submitted marker must be a JSON object")
                    submission_dto = parse_submission_record(sub_dict)
                    if submission_dto.command_id != command_id:
                        raise CorruptMarkerError(
                            "Submitted marker command_id does not match lookup key"
                        )
                    return CommandLookup(
                        state=CommandLookupState.PENDING,
                        submission=submission_dto,
                    )
                except CommandCodecError as exc:
                    logger.error("Corrupt submission record for command %s: %s", command_id, exc)
                    raise CorruptMarkerError(f"Corrupt submission record: {exc}") from exc
                except json.JSONDecodeError as exc:
                    logger.error("Corrupt JSON in submitted marker for command %s: %s", command_id, exc)
                    raise CorruptMarkerError(f"Corrupt JSON in submitted marker: {exc}") from exc

            # 3. Cả 2 key đều không tồn tại -> MISSING
            return CommandLookup(state=CommandLookupState.MISSING)

        except (CorruptMarkerError, ValueError):
            raise
        except redis.exceptions.RedisError as exc:
            logger.error("Redis lookup failed for command %s: %s", command_id, exc)
            raise ReaderRedisUnavailableError(f"Redis unavailable: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error during command lookup for %s: %s", command_id, exc)
            raise ReaderRedisUnavailableError(f"Unexpected Redis lookup error: {exc}") from exc

    async def close(self) -> None:
        pass
