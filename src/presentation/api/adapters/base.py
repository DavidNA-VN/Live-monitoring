from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from ..models import (
    AlertDTO,
    CommandResultDTO,
    CommandSubmissionDTO,
    RuntimeStatusDTO,
    StreamConfigDTO,
)


class CommandLookupState(str, Enum):
    PENDING = "PENDING"
    FINAL = "FINAL"
    MISSING = "MISSING"


@dataclass(frozen=True)
class CommandLookup:
    state: CommandLookupState
    submission: Optional[CommandSubmissionDTO] = None
    result: Optional[CommandResultDTO] = None


# Abstract class định nghĩa interface điều khiển Stream
# Giúp Presentation / API không phụ thuộc trực tiếp vào code lõi hoặc cách lưu trữ
class MonitoringControl(ABC):
    @abstractmethod
    async def start_stream(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        # Gửi lệnh bắt đầu theo dõi một stream
        pass

    @abstractmethod
    async def pause_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        # Gửi lệnh tạm dừng theo dõi
        pass

    @abstractmethod
    async def resume_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        # Gửi lệnh tiếp tục theo dõi
        pass

    @abstractmethod
    async def stop_stream(
        self,
        stream_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        # Gửi lệnh dừng theo dõi hoàn toàn
        pass

    @abstractmethod
    async def update_config(
        self,
        config: StreamConfigDTO,
        *,
        idempotency_key: Optional[str] = None,
    ) -> CommandSubmissionDTO:
        # Gửi lệnh cập nhật cấu hình stream
        pass

    async def close(self) -> None:
        # Dọn dẹp tài nguyên khi ứng dụng tắt
        pass


class CommandResultReader(ABC):
    @abstractmethod
    async def get_command_result(
        self,
        command_id: str,
    ) -> CommandLookup:
        pass

    async def close(self) -> None:
        pass


class RuntimeStatusReader(ABC):
    @abstractmethod
    async def get_status(self, stream_id: str) -> Optional[RuntimeStatusDTO]:
        pass

    async def close(self) -> None:
        pass


# Abstract class định nghĩa interface nguồn thông tin cảnh báo (Alert)
class AlertSource(ABC):
    @abstractmethod
    async def recent(self, stream_id: str, limit: int = 50) -> List[AlertDTO]:
        # Lấy danh sách cảnh báo gần đây để hiển thị khi người dùng tải lại trang
        pass

    @abstractmethod
    def subscribe(self, stream_id: str) -> AsyncIterator[AlertDTO]:
        # Đăng ký nhận luồng cảnh báo theo thời gian thực (cho WebSocket)
        pass

    async def stop_stream(self, stream_id: str) -> None:
        # Dừng và dọn dẹp các luồng alert liên quan đến stream_id
        pass

    async def close(self) -> None:
        # Dọn dẹp tài nguyên khi ứng dụng tắt
        pass
