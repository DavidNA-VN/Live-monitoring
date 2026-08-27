# Phase 4 Handoff - Command Observability & Backpressure

## 1. Muc tieu (Objectives)

Phase 4 giup he thong Command Pipeline cua Monitoring Worker co kha nang:
- **Quan sat & Chan doan (Observability & Diagnostics)**: Thu thap va cong bo metrics thoi gian thuc ve so luong command da nhan, trang thai xu ly (`APPLIED`, `NOOP`, `REJECTED`, `FAILED`), ty le duplicate replay, reclaim, dead-letter, thoi gian xu ly va tan suat poll Redis.
- **Tu bao ve & Chong tran bo nho (Backpressure & Guardrails)**: Chan cac payload vuot qua nguong cho phep (`max_command_payload_bytes`), kiem tra tuoi tho command (`max_command_age_seconds`), va bao ve Redis/Memory khoi viec sao chep raw payload qua lon vao Dead Letter Queue (DLQ).
- **Correlation Logging An Toan**: Ghi log day du dinh danh `worker_id`, `consumer_name`, `command_id`, `stream_id`, `action`, `status`, `changed`, `duration_ms`, `error_code` ma tuyet doi khong de lo master URL co chua token hay thong tin nhay cam.

---

## 2. Metrics Semantics & Redis Telemetry

### 1. Counter Semantics
- `command_delivery_received_total`: Tong so lan worker doc delivery tu Redis stream.
- `command_applied_total`: Command duoc thuc thi thanh cong va tao ra thay doi trang thai (`APPLIED` voi `changed=True`).
- `command_noop_total`: Command duoc thuc thi thanh cong nhung khong can thay doi do da o dung trang thai (`APPLIED` voi `changed=False`).
- `command_rejected_total`: Command bi tu choi do sai cau hinh, loi nghiep vu hop le, hoac qua han (`REJECTED`).
- `command_failed_total`: Command bi loi he thong bat thuong trong qua trinh thuc thi (`FAILED`).
- `command_reclaimed_total`: So luong command pending duoc worker thu hoi tu worker khac qua `XAUTOCLAIM`.
- `command_duplicate_replay_total`: So lan nhan lai command da tung xu ly voi cung ID va cung fingerprint.
- `command_dead_letter_total`: Tong so entry bi day vao DLQ do loi payload, loi JSON, ID trung lap khac payload, hoac loi nghiep vu bat thuong.
- `command_oversized_total`: Tong so command vuot qua nguong byte toi da.
- `command_stale_total`: Tong so command bi tu choi do qua han thoi gian `max_command_age_seconds`.
- `command_poll_failure_total`: Tong so lan gap loi Redis connection/poll.
- `command_processing_duration_ms_total`: Tong thoi gian xu ly cac command (ms).

### 2. Gauge & Timestamp Semantics
- `command_pending_count`: So luong command dang pending trong consumer group.
- `command_deferred_count`: So luong command tam hoan trong bo nho worker do loi tam thoi.
- `command_processing_duration_ms_max`: Thoi gian xu ly command lau nhat ghi nhan duoc (ms).
- `last_command_processing_duration_ms`: Thoi gian xu ly cua command gan nhat (ms).
- `last_successful_poll_at`: Thoi diem poll Redis thanh cong gan nhat (ISO-8601 UTC).
- `last_command_processed_at`: Thoi diem xu ly xong command gan nhat (ISO-8601 UTC).
- `last_error_code`: Ma loi gan nhat gap phai (hoac null neu khong co).

### 3. Redis Key & TTL
- Key: `media-monitor:worker:{worker_id}:command-metrics`
- Type: `HASH`
- Low Cardinality: Khong chua `stream_id` hoac `command_id` lam ten field.
- TTL: `120s` (tu dong bien mat neu worker ngung hoat dong).

---

## 3. Guardrails & Safe DLQ

### 1. Payload Size Guardrail (`max_command_payload_bytes`)
- Nguong mac dinh: `65_536` bytes (64 KB).
- Kiem tra do dai byte UTF-8 thuc te (`len(raw_payload.encode('utf-8'))`) truoc khi thuc hien JSON parsing.
- **Safe DLQ Metadata**: Khi payload vuot nguong, worker ghi nhan vao DLQ chi voi cac truong an toan:
  - `source_entry_id`
  - `payload_size_bytes`
  - `error_code`: `"COMMAND_PAYLOAD_TOO_LARGE"`
  - `error`: `"Command payload exceeds maximum allowed size"`
  - `failed_at`
  - **Khong luu tru raw payload qua kho** vao DLQ de tranh lam tran Redis memory.

### 2. Command Age Guardrail (`max_command_age_seconds`)
- Mac dinh: `None` (khong tu dong reject command de giu tuong thich nguoc).
- Khi duoc cau hinh (vi du 300s): Tu choi command neu `(now - requested_at) > max_command_age_seconds` voi ket qua `REJECTED`, `error_code="STALE_COMMAND"`.
- **Mien tru**: Cac command duoc thu hoi qua `XAUTOCLAIM` (`is_reclaimed=True`) duoc mien tru kiem tra tuoi tho de tranh reject nham cac command hop le bi ton dong sau su co worker/Redis.

---

## 4. Danh Sach File Trien Khai

| File | Vai tro |
|---|---|
| `contracts/worker-command-metrics.schema.json` | JSON Schema chuan hoa contract cong khai cho Worker Command Metrics. |
| `src/models/command_metrics.py` | Dataclass `CommandMetricsSnapshot` bat bien. |
| `src/core/command_metrics.py` | `CommandMetricsCollector` thu thap metrics thread-safe trong bo nho. |
| `src/app/command_guardrails.py` | `CommandGuardrails` kiem tra byte size va tuoi tho command truoc khi thuc thi. |
| `src/app/redis_worker_command_metrics_publisher.py` | `RedisWorkerCommandMetricsPublisher` cong bo snapshot metrics thanh Redis Hash voi TTL. |
| `src/core/redis_keys.py` | Bo sung helper `command_metrics(worker_id)` vao `WorkerRedisKeys`. |
| `src/app/redis_monitoring_command_consumer.py` | Tich hop guardrails, safe DLQ, do thoi gian xu ly va ghi nhan metrics tai tat ca cac diem chuyen trang thai. |
| `src/app/monitoring_worker.py` | Composition root khoi tao va wire guardrails, collector, publisher va consumer. |

---

## 5. Ket Qua Kiem Thu (Verification)

Ket qua trong phien review Phase 4:

- Targeted command metrics/guardrail/consumer/contract: **30 passed**.
- Full project regression: **419 passed, 31 skipped**.
- `git diff --check`: pass; canh bao LF/CRLF tren Windows khong phai whitespace error.
- Redis integration va worker E2E bi skip do disposable Redis khong san sang trong phien review. Can chay lai truoc production sign-off; khong duoc suy dien `pass` tu ket qua unit.

Lenh verification khi Redis da bat:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_worker_command_observability_integration.py -q
.\.venv\Scripts\python.exe scripts/run_worker_e2e.py fast
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 6. Quyet Dinh Sau Review

- Giu Redis blocking poll mac dinh `1000ms`; observability khong duoc lam tang poll/network traffic len 4 lan.
- Moi poll chi query pending count toi da mot lan cho telemetry; loi `XPENDING` phai propagate de readiness ve false va tang poll failure, khong duoc gia thanh poll thanh cong.
- Provenance `is_reclaimed` duoc giu khi command bi deferred va retry, tranh stale-reject nham command recovery.
- `FUTURE_COMMAND` la rejected command nhung khong bi dem vao `command_stale_total`.
- Consumer phu thuoc `CommandMetricsPublisher` protocol, khong phu thuoc Redis adapter concrete.
- Snapshot validate worker identity, counter/gauge va duration truoc khi serialize.
- Metrics Redis hash chi dung low-cardinality fields; khong dua `command_id` hay `stream_id` thanh field name.

## 7. Gioi Han Con Lai

- Command stream retention va API rate limiting thuoc backend/producer; worker khong tu `XTRIM` stream co consumer group.
- Redis metrics la operational telemetry co TTL, khong phai historical database.
- Multi-worker ownership, lease/fencing va load balancing khong thuoc Phase 4.
- Capacity calibration va command-flood soak test can chay tren target deployment environment.
