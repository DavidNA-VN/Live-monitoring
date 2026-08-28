# Presentation & Dashboard Documentation

Tài liệu này tổng hợp kiến trúc, hợp đồng dữ liệu và trạng thái của tầng Presentation (Live Monitoring Dashboard UI & FastAPI Service).

---

## 1. Giao diện Giám sát (UI/UX)
- **Phong cách thiết kế**: Dark Mode kết hợp kính mờ (Glassmorphism), Typography hiện đại (`Outfit` cho số liệu / tiêu đề, `Inter` cho nội dung).
- **Phân bổ layout**:
  - Trung tâm: Master HLS Video Player (HLS.js phát trực tiếp từ master playlist) và Telemetry Bar (Status, Resolution, FPS, Bitrate).
  - Cột phải: Event Log theo thời gian thực (hiển thị `BLACK_SCREEN`, `AUDIO_LOSS` và phân biệt trạng thái `OPEN` / `RESOLVED`).
- **Nút điều khiển**: Hỗ trợ Connect/Start, Pause, Resume, Stop.

---

## 2. Hợp đồng Dữ liệu Presentation (Phase 0 Baseline)

Tầng Presentation tách biệt hoàn toàn khỏi code lõi (detectors, supervisors, profiles) và giao tiếp qua các DTO tuân thủ chặt chẽ JSON Schema (`schema_version: "1.0"`):

### 2.1. Cấu hình Luồng (`StreamConfigDTO`)
```json
{
  "schema_version": "1.0",
  "stream_id": "channel-01",
  "master_url": "https://example.com/master.m3u8",
  "checks": {
    "black_screen": { "enabled": true },
    "audio_loss": {
      "enabled": true,
      "threshold_dbfs": -40.0,
      "duration_seconds": 5.0,
      "track_index": 0
    }
  }
}
```

### 2.2. Tiếp nhận Lệnh (`CommandSubmissionDTO`)
Khi UI gửi lệnh điều khiển (`POST /start`, `/pause`, `/resume`, `/stop`, `/config`), API trả về `202 Accepted`:
```json
{
  "schema_version": "1.0",
  "command_id": "8f6831d1-6c2e-48a6-896f-44eb1202e88a",
  "stream_id": "channel-01",
  "status": "ACCEPTED",
  "message": "Start command accepted for stream channel-01"
}
```

### 2.3. Kết quả Xử lý Lệnh (`CommandResultDTO`)
Tra cứu kết quả qua `GET /api/v1/commands/{command_id}`:
```json
{
  "schema_version": "1.0",
  "command_id": "8f6831d1-6c2e-48a6-896f-44eb1202e88a",
  "command_type": "START",
  "stream_id": "channel-01",
  "status": "APPLIED",
  "changed": true,
  "processed_at": "2026-08-28T08:15:00Z",
  "error_code": null,
  "error": null
}
```
*Trạng thái kết quả (`status`): `APPLIED`, `NOOP`, `REJECTED`, `FAILED`.*

### 2.4. Trạng thái Runtime (`RuntimeStatusDTO`)
Tra cứu qua `GET /api/v1/streams/{stream_id}/status`:
```json
{
  "schema_version": "1.0",
  "stream_id": "channel-01",
  "status": "RUNNING",
  "health": "HEALTHY",
  "started_at": "2026-08-28T08:15:00Z",
  "last_poll_at": "2026-08-28T08:15:05Z",
  "active_variant_count": 3,
  "queue_depth": 5,
  "queue_lag_seconds": 0.0,
  "error": null,
  "telemetry_available": true,
  "health_reasons": [],
  "checks": {
    "black_screen": "ENABLED",
    "audio_loss": "ENABLED"
  },
  "worker_id": "worker.node-01",
  "observed_at": "2026-08-28T08:15:05Z"
}
```

### 2.5. Cảnh báo Lịch sử (`AlertDTO`) và WebSocket Realtime (`AlertMessageDTO`)
Lấy gần đây qua `GET /api/v1/streams/{stream_id}/events`: danh sách `AlertDTO`.
Đẩy qua WebSocket `/api/v1/ws/streams/{stream_id}`: bao gói trong `AlertMessageDTO`:
```json
{
  "message_type": "ALERT",
  "stream_id": "channel-01",
  "payload": {
    "schema_version": "1.0",
    "alert_id": "e9b53e7f-4447-498c-bb04-09852264c8f5",
    "event_id": "evt-channel-01",
    "category": "content",
    "event_type": "BLACK_SCREEN",
    "state": "OPEN",
    "stream_id": "channel-01",
    "check": "black_screen",
    "occurred_at": "2026-08-28T08:16:00Z",
    "emitted_at": "2026-08-28T08:16:00Z",
    "event_started_at": "2026-08-28T08:16:00Z",
    "event_ended_at": null,
    "reason": "black_screen_detected",
    "attributes": {}
  }
}
```

---

## 3. Kiến trúc Triển khai & Lifecycle
- **App Factory**: `create_app(...)` inject riêng command control, command-result reader, runtime-status reader và alert source. Fake dùng chung một in-memory adapter; production có thể cắm từng Redis adapter độc lập.
- **Lifespan Manager**: Đảm bảo đóng kết nối và hủy sạch các background task khi ứng dụng tắt, không rò rỉ tài nguyên.
- **Per-stream Lifecycle**: Các tiến trình sinh alert hoặc subcriber queue được quản lý cô lập theo từng `stream_id`, dọn dẹp triệt để khi `stop_stream`.

---

## 4. Lộ trình Tích hợp tiếp theo
- **Phase 1**: Xây dựng Redis Command Gateway (`redis_monitoring_control.py` & `redis_command_result_reader.py`).
- **Phase 2**: Xây dựng Public Runtime Status API (`redis_runtime_status.py`).
- **Phase 3**: Xây dựng Alert History & WebSocket Gateway (`redis_alert_source.py`).
- **Phase 4**: FastAPI Composition, Dependency Configuration & Structured Logging.
- **Phase 5**: Cập nhật Frontend Dashboard đồng bộ với real-backend lifecycle.
- **Phase 6**: Test Harness MVP E2E hoàn chỉnh.
