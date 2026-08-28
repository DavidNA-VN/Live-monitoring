# PHASE 5: FRONTEND REAL-BACKEND LIFECYCLE CONTRACT

## 1. Overview & Frontend State Machine

Dashboard UI giao tiếp với Backend thông qua máy trạng thái rõ ràng, không coi `202 ACCEPTED` là stream đã chạy thành công mà theo dõi toàn bộ quá trình xử lý lệnh bất đồng bộ:

```text
               ┌────────────────────────── IDLE ─────────────────────────┐
               │                                                         ▲
         User START                                                  User STOP
               │                                                         │
               ▼                                                         │
        SUBMITTING_START                                          SUBMITTING_STOP
               │                                                         │
       202 + command_id                                           Stop command
               │                                                         │
               ▼                                                         │
      WAITING_START_RESULT ──[REJECTED/FAILED]──► FAILED                 │
               │                                                         │
         APPLIED/NOOP                                                    │
               │                                                         │
               ▼                                                         │
            STARTING ─────────[Timeout/Error]───► FAILED                 │
               │                                                         │
        Status RUNNING                                                   │
               │                                                         │
               ▼                                                         │
            RUNNING ◄─────────────── Resume APPLIED ───────────────┐     │
               │                                                   │     │
          User PAUSE                                          User RESUME│
               │                                                   │     │
               ▼                                                   │     │
        SUBMITTING_PAUSE                                  SUBMITTING_RESUME
               │                                                   │     │
         Pause APPLIED                                             │     │
               │                                                   │     │
               ▼                                                   │     │
            PAUSED ────────────────────────────────────────────────┴─────┘
```

---

## 2. Asynchronous Command & Status Workflow

1. **Gửi lệnh (`POST /start`, `/pause`, `/resume`, `/stop`)**:
   - Gửi kèm header `Idempotency-Key: <UUID>`.
   - Nhận phản hồi `202 ACCEPTED` chứa `command_id`.
2. **Theo dõi kết quả lệnh (`GET /api/v1/commands/{command_id}`)**:
   - Polling với chu kỳ `500ms`, tối đa `30s` (hỗ trợ `AbortController`).
   - Xử lý các trạng thái:
     - `APPLIED` / `NOOP`: Tiếp tục đồng bộ trạng thái runtime.
     - `REJECTED` / `FAILED`: Chuyển state sang `FAILED` và hiển thị thông báo lỗi chi tiết.
3. **Đồng bộ trạng thái runtime (`GET /api/v1/streams/{stream_id}/status`)**:
   - Chỉ khi status API xác nhận luồng đã `RUNNING`, Frontend mới kích hoạt HLS Video Player và kết nối WebSocket Realtime.

---

## 3. Media & WebSocket Realtime Gateway

### 3.1. HLS Player & Web Audio API
- Sử dụng `Hls.js` để phát video HLS.
- Web Audio API trích xuất mức âm lượng hiển thị thanh Audio Meter.
- **Fake Telemetry Gating**: Chỉ cho phép sinh telemetry giả lập (FPS, fake audio meter) khi server hoạt động ở `fake` mode (được xác nhận qua `/health/ready`). Tuyệt đối không chạy fake telemetry trong `redis` production mode.

### 3.2. WebSocket & Alert Envelope
- Endpoint: `/api/v1/ws/streams/{stream_id}`.
- Đọc dữ liệu từ envelope chuẩn `AlertMessageDTO`:
  ```javascript
  const alert = (data.message_type === 'ALERT') ? data.payload : data;
  ```
- **Deduplication**: Lưu trữ tối đa 1000 `alert_id` gần nhất để loại bỏ alert trùng lặp giữa WebSocket và REST history.
- **Reconnect Backoff**: Exponential backoff (1s $\to$ 2s $\to$ 4s $\to$ 8s $\to$ max 15s) và reset về 1s khi kết nối thành công.
- **Gap Backfilling**: Khi kết nối hoặc reconnect thành công, tự động gọi `GET /api/v1/streams/{stream_id}/events?limit=50` để nạp các alerts phát sinh trong thời gian gián đoạn mạng.

---

## 4. Idempotent Resource Cleanup on STOP

Khi người dùng bấm **STOP** hoặc chuyển đổi stream, hàm `disposeCurrentSession()` được thực thi với các bước tuần tự và triệt để:
1. Đặt cờ `shouldReconnect = false` và ngắt kết nối WebSocket.
2. Hủy HLS player instance (`hls.destroy()`).
3. Dừng và giải phóng video element (`video.pause()`, `video.removeAttribute('src')`, `video.load()`).
4. Hủy `requestAnimationFrame` và đóng/suspend `AudioContext`.
5. Hủy toàn bộ polling timers (`setInterval`, `setTimeout`).
6. Kích hoạt `AbortController.abort()` để hủy các HTTP request đang chờ.
7. Đặt lại toàn bộ telemetry và trạng thái nút bấm về `IDLE`.

---

## 5. XSS Prevention & DOM Security

- Tuyệt đối cấm sử dụng `innerHTML` khi chèn dữ liệu động (tên luồng, reason, payload attributes, error messages).
- Toàn bộ giao diện và Event Log được dựng qua các API an toàn: `document.createElement()`, `document.createTextNode()`, và `textContent`.
- Phân loại trực quan rõ ràng qua CSS badges: `OPEN`, `RESOLVED`, `DEGRADED`, `RECOVERED`.

---

## 6. Failure And Cleanup Semantics

- `PAUSE` và `RESUME` chỉ đổi UI state sau khi command final là `APPLIED/NOOP` và runtime status đạt state đích.
- `STOP` chỉ dọn local media/WebSocket sau khi command final là `APPLIED/NOOP` và public runtime status trả `404`.
- Nếu STOP submission hoặc command thất bại, frontend giữ session hiện tại để người dùng retry; không hiển thị dừng thành công giả.
- Nếu readiness không đọc được mode, frontend dùng `unknown`, tuyệt đối không fallback sang fake telemetry.
- Web Audio graph được tái sử dụng giữa các lần START trên cùng video element; STOP suspend graph và page unload mới đóng hoàn toàn AudioContext.
- JavaScript lifecycle regression tests chạy bằng:
  `node --experimental-default-type=module --test tests/frontend/test_lifecycle_controller.mjs`.
