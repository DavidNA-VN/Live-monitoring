import asyncio
from datetime import datetime, timezone
import uuid
from typing import AsyncGenerator, Dict, List, Optional

from .base import MonitoringControl, AlertSource
from ..models import (
    AlertCategory, AlertDTO, AlertState, EventType,
    RuntimeStatusDTO, StreamConfigDTO, StreamHealthEnum, StreamStatusEnum
)

# Implementation giả (Fake) để điều khiển stream trong bộ nhớ RAM, dùng cho MVP
class FakeMonitoringControl(MonitoringControl):
    def __init__(self):
        self._streams: Dict[str, StreamConfigDTO] = {}
        self._statuses: Dict[str, RuntimeStatusDTO] = {}
        
    async def start_stream(self, config: StreamConfigDTO) -> bool:
        # Lưu config vào memory và tạo status giả đang chạy (RUNNING)
        self._streams[config.stream_id] = config
        self._statuses[config.stream_id] = RuntimeStatusDTO(
            stream_id=config.stream_id,
            status=StreamStatusEnum.RUNNING,
            health=StreamHealthEnum.HEALTHY,
            started_at=datetime.now(timezone.utc),
            last_poll_at=datetime.now(timezone.utc),
            active_variant_count=3,
            queue_depth=5,
            telemetry_available=True,
            checks={
                "black_screen": "ENABLED" if config.checks.black_screen.enabled else "DISABLED",
                "audio_loss": "ENABLED" if config.checks.audio_loss.enabled else "DISABLED"
            }
        )
        return True

    async def pause_stream(self, stream_id: str) -> bool:
        if stream_id in self._statuses:
            self._statuses[stream_id].status = StreamStatusEnum.PAUSED
            return True
        return False

    async def resume_stream(self, stream_id: str) -> bool:
        if stream_id in self._statuses:
            self._statuses[stream_id].status = StreamStatusEnum.RUNNING
            return True
        return False

    async def stop_stream(self, stream_id: str) -> bool:
        if stream_id in self._statuses:
            self._statuses[stream_id].status = StreamStatusEnum.STOPPED
            return True
        return False

    async def get_status(self, stream_id: str) -> Optional[RuntimeStatusDTO]:
        return self._statuses.get(stream_id)

# Implementation giả sinh ra alert ảo để test giao diện WebSocket
class FakeAlertSource(AlertSource):
    def __init__(self):
        self._recent: Dict[str, List[AlertDTO]] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._running = False
        
    def _create_alert(self, stream_id: str, state: AlertState, event_type: EventType = EventType.BLACK_SCREEN) -> AlertDTO:
        # Hàm tạo ra một cục dữ liệu Alert giả
        now = datetime.now(timezone.utc)
        return AlertDTO(
            alert_id=str(uuid.uuid4()),
            event_id=f"evt-{stream_id}",
            category=AlertCategory.CONTENT,
            event_type=event_type,
            state=state,
            stream_id=stream_id,
            occurred_at=now,
            emitted_at=now,
            event_started_at=now if state == AlertState.OPEN else None,
            reason="fake_threshold_reached"
        )
        
    async def recent(self, stream_id: str, limit: int = 50) -> List[AlertDTO]:
        return self._recent.get(stream_id, [])[-limit:]

    async def subscribe(self, stream_id: str) -> AsyncGenerator[AlertDTO, None]:
        if stream_id not in self._subscribers:
            self._subscribers[stream_id] = []
            
        queue = asyncio.Queue()
        self._subscribers[stream_id].append(queue)
        
        try:
            while True:
                # Đợi cho đến khi có alert mới được push vào queue
                alert = await queue.get()
                yield alert
        finally:
            if queue in self._subscribers.get(stream_id, []):
                self._subscribers[stream_id].remove(queue)
                
    async def start_generating_fake_alerts(self, stream_id: str):
        # Hàm này chạy ngầm (background task) cứ 10 giây bắn lỗi, 5 giây sau báo đã fix (RESOLVED)
        self._running = True
        if stream_id not in self._recent:
            self._recent[stream_id] = []
            
        while self._running:
            await asyncio.sleep(10) # 10s bình thường
            if stream_id in self._subscribers and self._subscribers[stream_id]:
                # Giả lập lỗi màn hình đen (OPEN)
                alert = self._create_alert(stream_id, AlertState.OPEN, EventType.BLACK_SCREEN)
                self._recent[stream_id].append(alert)
                for q in self._subscribers[stream_id]:
                    await q.put(alert)
                
                await asyncio.sleep(5) # 5s sau thì hết lỗi
                
                # Giả lập hết lỗi (RESOLVED)
                alert_resolved = self._create_alert(stream_id, AlertState.RESOLVED, EventType.BLACK_SCREEN)
                self._recent[stream_id].append(alert_resolved)
                for q in self._subscribers[stream_id]:
                    await q.put(alert_resolved)
