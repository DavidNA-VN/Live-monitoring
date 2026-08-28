# PHASE 6: MVP E2E VERIFICATION CONTRACT

## 1. Overview & Full-Stack Architecture

Phase 6 xác minh chuỗi mắt xích hoàn chỉnh của hệ thống Live Media Monitor MVP từ giao diện người dùng đến luồng xử lý video/audio thực tế:

```text
┌─────────────────────────── Dashboard UI (Browser) ───────────────────────────┐
│  - ES6 Modular Dashboard (State Machine IDLE -> STARTING -> RUNNING -> STOP) │
│  - HLS.js Video Player + Web Audio API Level Meter                          │
│  - Realtime Event Log (OPEN / RESOLVED)                                      │
└───────────────────────┬───────────────────────────────▲──────────────────────┘
                        │ REST Commands                 │ WebSocket & REST History
                        ▼                               │
┌─────────────────────────── FastAPI Gateway Service ──────────────────────────┐
│  - Mode: "redis" (MONITORING_API_MODE=redis)                                 │
│  - Health Probes: /health/live (200), /health/ready (200 / 503)              │
│  - Single Shared Async Redis Connection                                      │
└───────────────────────┬───────────────────────────────▲──────────────────────┘
                        │ XADD commands                 │ HGET status / XREAD outbox
                        ▼                               │
┌─────────────────────────── Shared Redis Keyspace ────────────────────────────┐
│  - media-monitor:v1:control:commands (Stream)                                │
│  - media-monitor:v1:control:commands:result:<id> (Marker Key)                │
│  - media-monitor:v1:public:runtime-status (Hash)                             │
│  - media-monitor:v1:alerts:outbox (Stream)                                   │
└───────────────────────┬───────────────────────────────▲──────────────────────┘
                        │ XREADGROUP commands           │ XADD outbox / HSET status
                        ▼                               │
┌─────────────────────────── Monitoring Worker Service ────────────────────────┐
│  - Supervisor & Session Management                                           │
│  - FFmpeg Media Pipeline (Black-screen & Audio-loss Detectors)               │
│  - Public Runtime Projection Service                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Three-Tier Testing Matrix

### Tier 1: Automated Local E2E (`tests/e2e/test_mvp_api_worker_e2e.py`)

Chạy tự động thông qua script điều phối:
```powershell
# Chạy nhanh Presentation & Contract Tests
python scripts/run_mvp_e2e.py fast

# Chạy E2E Tests đầy đủ với Worker + FFmpeg + Redis
python scripts/run_mvp_e2e.py media

# Chạy toàn bộ test suites
python scripts/run_mvp_e2e.py all
```

#### Hai kịch bản kiểm thử cốt lõi:
1. **Black Screen E2E**:
   - Khởi động HTTP server & live black screen publisher.
   - Gửi `POST /api/v1/streams/{id}/start` qua FastAPI $\to$ `202 ACCEPTED` + `command_id`.
   - Polling `GET /api/v1/commands/{command_id}` $\to$ `APPLIED`.
   - Polling `GET /api/v1/streams/{id}/status` $\to$ `RUNNING`.
   - WebSocket `/api/v1/ws/streams/{id}` nhận `AlertMessageDTO` với `event_type="BLACK_SCREEN"`, `state="OPEN"`.
   - Khi hình ảnh trở lại, WebSocket nhận `RESOLVED` với cùng `event_id`.
   - Duplicate START với cùng config trả command result `NOOP`.
   - REST History `GET /api/v1/streams/{id}/events` trả về alert tương ứng.
   - `POST /stop` $\to$ status trả về `404 Not Found` $\to$ clean shutdown.
2. **Audio Loss E2E**:
   - Khởi động HTTP server & live audio silence publisher.
   - `POST /start` $\to$ `APPLIED` $\to$ `RUNNING` $\to$ WebSocket nhận `AUDIO_LOSS` `OPEN`.
   - `POST /pause` $\to$ status `PAUSED`.
   - `POST /resume` $\to$ status `RUNNING`.
   - `POST /stop` $\to$ status `404 Not Found` $\to$ clean shutdown.

---

### Tier 2: Manual 4-Terminal Browser Test với Local Publisher

Hướng dẫn chạy kiểm thử thực tế trên 4 terminal riêng biệt từ thư mục gốc dự án:

#### Terminal 1: HLS Static Server
```powershell
.\.venv\Scripts\Activate.ps1
python scripts\serve_hls.py
```

#### Terminal 2: Live HLS Black-Screen Publisher
```powershell
.\.venv\Scripts\Activate.ps1
python scripts\publish_live_hls.py --source hls_output\output --output hls_output\live_black_loop --loop --reset
```

#### Terminal 3: Monitoring Worker Process
```powershell
.\.venv\Scripts\Activate.ps1
$env:REDIS_URL="redis://localhost:6379/0"
python src\live_main.py --command-worker --worker-id worker-mvp-01 --redis-prefix media-monitor:mvp:test --max-streams 1 --max-service-media-processes 4
```

#### Terminal 4: FastAPI Presentation Service & Dashboard
```powershell
.\.venv\Scripts\Activate.ps1
$env:MONITORING_API_MODE="redis"
$env:REDIS_URL="redis://localhost:6379/0"
$env:REDIS_PREFIX="media-monitor:mvp:test"
$env:ENABLE_FAKE_GENERATOR="false"

python -m uvicorn presentation.api.main:app --app-dir src --host 127.0.0.1 --port 8080
```

#### Thao tác trên trình duyệt:
1. Mở trình duyệt tại địa chỉ: `http://127.0.0.1:8080`.
2. Kiểm tra badge hiển thị: `MODE: REDIS`.
3. Nhập:
   - **Stream ID**: `mvp-local-01`
   - **Master URL**: `http://127.0.0.1:8000/live_black_loop/master.m3u8`
4. Bấm **Connect**:
   - Trạng thái chuyển từ `CONNECTING` sang `RUNNING`.
   - Video player bắt đầu phát và Event Log nhận `[OPEN] [BLACK_SCREEN]`.
5. Bấm **Pause**: Trạng thái chuyển sang `PAUSED`, video tạm dừng.
6. Bấm **Resume**: Trạng thái trở lại `RUNNING`, video tiếp tục phát.
7. Bấm **Stop**: Trạng thái chuyển về `IDLE`, player được giải phóng, status trả về 404.

---

### Tier 3: External Live Stream Smoke Testing

- Sử dụng các luồng HLS công khai (không DRM, không token/cookie).
- **Phân biệt ranh giới CORS**:
  - Worker FFmpeg phân tích stream trực tiếp từ mạng (không chịu hạn chế CORS của browser).
  - Trình duyệt cần CORS headers từ origin server để phát video và đọc AudioContext. Nếu origin chặn CORS, video có thể không phát trên UI nhưng worker vẫn giám sát và phát hiện sự cố bình thường.

---

## 3. Definition of Done Checklist

- [ ] Local black-screen và audio-loss E2E tests hoàn thành và passed trên disposable Redis.
- [x] Dashboard UI không chuyển `RUNNING` trước khi runtime status xác nhận.
- [ ] REST History và WebSocket đều nhận đúng Alert thật từ Worker outbox stream.
- [ ] PAUSE / RESUME / STOP hoạt động chính xác qua Command Worker thật.
- [x] STOP dọn dẹp sạch sẽ tài nguyên, không để lại background tasks hoặc reconnect loop.
- [x] Redis, API và Worker dùng chung một prefix (`RedisNamespace`).
- [x] Không sinh fake alert hoặc fake telemetry trong `redis` production mode.
- [x] Direct dependencies được pin chặt chẽ.
- [ ] `python scripts/run_mvp_e2e.py media` pass mà không có test bị skip.
- [ ] Toàn bộ test suites pass, không có regression.
- [x] `git diff --check` pass.

`media` và `all` là release gates: runner đặt `REQUIRE_MVP_E2E=1`, vì vậy thiếu
Redis/FFmpeg phải làm lệnh thất bại thay vì trả về false green với toàn bộ test bị skip.
