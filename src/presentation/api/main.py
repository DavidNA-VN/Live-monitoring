import asyncio
from typing import Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import os

from .models import StreamConfigDTO, RuntimeStatusDTO, AlertDTO
from .adapters.fakes import FakeMonitoringControl, FakeAlertSource

# Khởi tạo ứng dụng FastAPI
app = FastAPI(
    title="Live Monitoring API",
    description="API điều khiển và giám sát luồng live HLS",
    version="1.0.0"
)

# Khởi tạo các adapter giả (Fake Adapter) để test MVP
control_adapter = FakeMonitoringControl()
alert_adapter = FakeAlertSource()

# Biến để đánh dấu xem tiến trình sinh alert giả đã chạy cho stream nào chưa
# Dùng để test giao diện WebSocket nhấp nháy đỏ/xanh
_fake_alert_tasks: Dict[str, asyncio.Task] = {}

# Đường dẫn tới thư mục chứa giao diện HTML/CSS/JS
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")

# Gắn thư mục static vào đường dẫn /static
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    # Phục vụ trang Dashboard HTML khi người dùng vào trang chủ
    with open(os.path.join(static_dir, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.post("/streams/{stream_id}/start", status_code=202)
async def start_stream(stream_id: str, config: StreamConfigDTO):
    # API Bắt đầu giám sát một luồng mới
    if stream_id != config.stream_id:
        raise HTTPException(status_code=400, detail="Mã stream_id trên URL và trong body không khớp nhau")
    
    success = await control_adapter.start_stream(config)
    if not success:
        raise HTTPException(status_code=500, detail="Không thể bắt đầu stream")
        
    # Bật tiến trình background sinh dữ liệu lỗi giả để test UI nếu chưa bật
    if stream_id not in _fake_alert_tasks:
        _fake_alert_tasks[stream_id] = asyncio.create_task(
            alert_adapter.start_generating_fake_alerts(stream_id)
        )
    return {"message": f"Đã gửi lệnh bắt đầu giám sát luồng {stream_id}"}

@app.post("/streams/{stream_id}/pause", status_code=202)
async def pause_stream(stream_id: str):
    # API Tạm dừng
    success = await control_adapter.pause_stream(stream_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy stream này")
    return {"message": "Đã tạm dừng"}

@app.post("/streams/{stream_id}/resume", status_code=202)
async def resume_stream(stream_id: str):
    # API Tiếp tục
    success = await control_adapter.resume_stream(stream_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy stream này")
    return {"message": "Đã tiếp tục"}

@app.post("/streams/{stream_id}/stop", status_code=202)
async def stop_stream(stream_id: str):
    # API Dừng hẳn
    success = await control_adapter.stop_stream(stream_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy stream này")
    return {"message": "Đã dừng hoàn toàn"}

@app.get("/streams/{stream_id}/status", response_model=RuntimeStatusDTO)
async def get_stream_status(stream_id: str):
    # API Lấy trạng thái sức khỏe (health, variant_count, ...)
    status = await control_adapter.get_status(stream_id)
    if not status:
        raise HTTPException(status_code=404, detail="Không tìm thấy stream này")
    return status

@app.get("/streams/{stream_id}/events", response_model=list[AlertDTO])
async def get_stream_events(stream_id: str):
    # API Lấy lịch sử sự kiện gần đây khi giao diện vừa load hoặc reconnect
    return await alert_adapter.recent(stream_id)

@app.websocket("/ws/streams/{stream_id}")
async def websocket_endpoint(websocket: WebSocket, stream_id: str):
    # WebSocket dùng để đẩy dữ liệu realtime (sự kiện, cảnh báo) về cho trình duyệt (Dashboard)
    await websocket.accept()
    try:
        # Bắt đầu nghe (subscribe) các sự kiện từ alert_adapter
        async for alert in alert_adapter.subscribe(stream_id):
            # Biến Pydantic model thành JSON và đẩy qua WebSocket
            await websocket.send_json(alert.model_dump(mode="json"))
    except WebSocketDisconnect:
        # Trình duyệt đóng kết nối (ví dụ user tắt tab hoặc đứt mạng)
        print(f"Client đã ngắt kết nối WebSocket luồng {stream_id}")
