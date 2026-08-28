# PHASE 4: FASTAPI LIFECYCLE, CONFIGURATION & OPERATIONS CONTRACT

## 1. Overview & Operational Modes

Live Monitoring API hỗ trợ hai chế độ hoạt động minh bạch, được chọn thông qua cấu hình môi trường:

```text
                               ┌────────────── ApiSettings ─────────────┐
                               │ MONITORING_API_MODE=fake|redis        │
                               └───────────────────┬────────────────────┘
                                                   │
                         ┌─────────────────────────┴─────────────────────────┐
                         ▼                                                   ▼
                Mode: "fake"                                        Mode: "redis"
         (UI Dev & Isolated Tests)                            (Production Operation)
  ┌─────────────────────────────────────┐             ┌─────────────────────────────────────┐
  │ • FakeMonitoringControl             │             │ • 1 Async Redis Client/Pool         │
  │ • FakeAlertSource                   │             │ • RedisMonitoringControl            │
  │ • Fake alert generator (optional)   │             │ • RedisCommandResultReader          │
  │ • No Redis connection               │             │ • RedisRuntimeStatusReader          │
  │ • Readiness: always 200 OK          │             │ • RedisAlertSource                  │
  └─────────────────────────────────────┘             │ • Readiness: Redis PING (200 / 503) │
                                                      │ • NO fallback to fake               │
                                                      └─────────────────────────────────────┘
```

---

## 2. Configuration (`ApiSettings`)

Cấu hình được quản lý qua `ApiSettings.from_env()`:

| Environment Variable | Default Value | Type | Description |
| :--- | :--- | :--- | :--- |
| `MONITORING_API_MODE` | `fake` | `Literal["fake", "redis"]` | Chế độ khởi động API (Bắt buộc `fake` hoặc `redis`, mode lạ sẽ fail startup). |
| `REDIS_URL` | `redis://localhost:6379/0` | `str` | URL kết nối Redis server (chỉ yêu cầu khi mode là `redis`). |
| `REDIS_PREFIX` | `media-monitor:v1` | `str` | Keyspace prefix chung đồng bộ giữa API và Worker. |
| `ALERT_HISTORY_SCAN_LIMIT` | `1000` | `int > 0` | Giới hạn số entries quét tối đa trong paginated XREVRANGE. |
| `WEBSOCKET_REDIS_BLOCK_MS` | `1000` | `int > 0` | Block timeout cho lệnh XREAD trong WebSocket subscription. |
| `ENABLE_FAKE_GENERATOR` | `false` | `bool` | Tự động sinh alert giả lập (chỉ áp dụng trong fake mode và phải bật tường minh). |

---

## 3. Composition Root & One-Owner Lifecycle

- **One-Owner Connection Rule**:
  - `build_dependencies` tạo duy nhất **một** async Redis client và inject vào toàn bộ 4 Redis adapters.
  - Adapter `close()` chỉ dọn dẹp các async tasks / subscriptions của chính adapter đó và **không** tự ý đóng shared Redis client.
  - `PresentationDependencies.close()` chịu trách nhiệm đóng Redis client khi FastAPI shutdown.
- **Thứ tự dọn dẹp (Shutdown Cleanup Order)**:
  1. Đóng `alert_source` trước (hủy các blocking XREAD / subscription tasks).
  2. Đóng các adapter còn lại (`control`, `command_results`, `status_reader`), đảm bảo một instance không bị đóng trùng lặp.
  3. Đóng `redis_client` cuối cùng.
  4. Bắt và log riêng từng lỗi resource mà không ngắt quãng toàn bộ quá trình shutdown.

---

## 4. Health & Readiness Probes

### 4.1. Liveness Probe (`GET /health/live`)
- Xác nhận tiến trình API và asyncio event loop đang hoạt động.
- Không phụ thuộc vào Redis (luôn trả về `200 OK` kể cả khi Redis đang offline):
```json
{
  "status": "alive"
}
```

### 4.2. Readiness Probe (`GET /health/ready`)
- Xác nhận service đã sẵn sàng tiếp nhận traffic của người dùng:
  - **Fake Mode**: Trả về `200 OK`:
    ```json
    {
      "status": "ready",
      "mode": "fake"
    }
    ```
  - **Redis Mode (Redis hoạt động)**: Trả về `200 OK`:
    ```json
    {
      "status": "ready",
      "mode": "redis",
      "dependencies": {
        "redis": "available"
      }
    }
    ```
  - **Redis Mode (Redis down hoặc timeout > 1.0s)**: Trả về `503 Service Unavailable`:
    ```json
    {
      "status": "not_ready",
      "mode": "redis",
      "dependencies": {
        "redis": "unavailable"
      }
    }
    ```

---

## 5. Security, Logging & Static Files

- **Sanitized Logging**:
  - Tuyệt đối không log toàn bộ `REDIS_URL` (có thể chứa mật khẩu), token/query param trong HLS URL, credentials, hay raw payload.
  - Lỗi hạ tầng (Redis outage, corrupt payload) trả về client message chuẩn hóa: `{"detail": "Monitoring service is temporarily unavailable"}`.
- **Static Assets Resolution**:
  - File tĩnh và Dashboard HTML được mount dựa trên đường dẫn tuyệt đối chuẩn hóa (`Path(__file__).resolve().parent.parent / "static"`), đảm bảo hoạt động nhất quán bất kể working directory khi khởi chạy tiến trình.
