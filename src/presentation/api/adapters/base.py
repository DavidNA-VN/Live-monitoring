from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Optional
from ..models import StreamConfigDTO, RuntimeStatusDTO, AlertDTO

# Abstract class định nghĩa interface điều khiển Stream
# Giúp Backend (API) không phụ thuộc trực tiếp vào code lõi
class MonitoringControl(ABC):
    @abstractmethod
    async def start_stream(self, config: StreamConfigDTO) -> bool:
        # Bắt đầu theo dõi một stream
        pass

    @abstractmethod
    async def pause_stream(self, stream_id: str) -> bool:
        # Tạm dừng theo dõi
        pass

    @abstractmethod
    async def resume_stream(self, stream_id: str) -> bool:
        # Tiếp tục theo dõi
        pass

    @abstractmethod
    async def stop_stream(self, stream_id: str) -> bool:
        # Dừng hẳn
        pass

    @abstractmethod
    async def get_status(self, stream_id: str) -> Optional[RuntimeStatusDTO]:
        # Lấy trạng thái hiện tại (Running, Stopped, Health, v.v.)
        pass

# Abstract class định nghĩa interface lấy thông tin cảnh báo (Alert)
class AlertSource(ABC):
    @abstractmethod
    async def recent(self, stream_id: str, limit: int = 50) -> List[AlertDTO]:
        # Lấy danh sách cảnh báo gần đây để hiển thị khi người dùng F5 tải lại trang
        pass

    @abstractmethod
    async def subscribe(self, stream_id: str) -> AsyncGenerator[AlertDTO, None]:
        # Đăng ký nhận luồng cảnh báo theo thời gian thực (để dùng cho WebSocket)
        pass
