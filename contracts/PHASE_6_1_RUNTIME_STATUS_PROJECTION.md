# Phase 1 Handoff - Public Runtime Status Projection

## 1. Kien truc (Architecture)

Phase 1 chuyen hoa `RuntimeStatusReader` (von la in-memory capability trong engine) thanh mo hinh doc cong khai (Public Read Model) phan tan tren Redis ma Backend / API co the truy cap truc tiep khong can import engine code:

```text
StreamSupervisor (In-Memory Engine State)
      |
      | active stream IDs & sessions
      v
SupervisorRuntimeStatusReader
      |
      | RuntimeStatus internal DTO
      v
RuntimeStatusProjectionService
      |
      | Enriches worker_id + observed_at (UTC)
      | Tracks known streams & detects removal
      v
RedisRuntimeStatusProjector (Deduplicating Port Adapter)
      |
      +---> HSET media-monitor:v1:public:runtime-status <stream_id> <RuntimeStatus>
      |     (Always refreshed with new observed_at; consumed for initial state/REST)
      |
      +---> XADD media-monitor:v1:public:runtime-status-updates payload <SNAPSHOT>
      |     (Published ONLY when semantic state changes; consumed by WebSocket gateway)
      |
      +---> Atomic REMOVED (XADD REMOVED + HDEL current status)
            (Published when stream is STOPPED/deleted after worker_id verification)
```

---

## 2. Redis Key Spaces

`PublicRuntimeRedisKeys` so huu toan bo key cong khai phuc vu backend integration:

| Data | Redis Type | Key Pattern | TTL / Retention |
|---|---|---|---|
| **Current Statuses** | `HASH` | `media-monitor:v1:public:runtime-status` | Persistent (HDEL on STOP) |
| **Status Updates** | `STREAM` | `media-monitor:v1:public:runtime-status-updates` | Approximate `MAXLEN ~ 10000` |

*Luu y: `RuntimeRedisKeys` van tiep tuc phuc vu internal metrics/health va hoan toan doc lap voi `PublicRuntimeRedisKeys`.*

---

## 3. Current Hash Semantics

- **Key**: `media-monitor:v1:public:runtime-status`
- **Field**: `stream_id` (External business identity, vi du `channel-01`).
- **Value**: JSON string tuan thu `contracts/runtime-status.schema.json`.
- **Muc dich su dung**:
  - Backend vua khoi dong (initial load).
  - UI Dashboard vua mo / browser refresh.
  - WebSocket client reconnect.
  - REST endpoint: `GET /streams/{stream_id}/status`.
- **Freshness**: Hash luon duoc cap nhat truong `observed_at` moi nhat (mac dinh moi 2 giay) de Backend biet trang thai delivery van dang fresh.

---

## 4. Status Update Stream Semantics

- **Key**: `media-monitor:v1:public:runtime-status-updates`
- **Field**: `payload` chua JSON string tuan thu `contracts/runtime-status-update.schema.json`.
- **Loai event (`update_type`)**:
  - `SNAPSHOT`: Phat ra khi stream co su thay doi ve trang thai hoac metric thuc su (status, health, checks, queue_depth, error, v.v.). Chua nested `status` payload day du.
  - `REMOVED`: Phat ra khi stream bi STOP hoac bi go bo khoi supervisor. Khong chua nested `status`.
- **Muc dich su dung**:
  - Backend stream consumer (`XREAD` / `XREADGROUP`) doc va day realtime vao WebSocket hub toi client dashboard.

---

## 5. Semantic Deduplication Rule

De tranh gay spam Redis Stream va nghen WebSocket khi hang tram stream dang o trang thai on dinh:
1. **Fingerprint Calculation**:
   - Projector trich xuat `semantic_state = {k: v for k, v in status.to_dict().items() if k != "observed_at"}`.
   - So sanh voi semantic state cua snapshot cu dang luu trong Hash.
2. **Behavior khi Semantic State KHONG doi**:
   - Chi goi `HSET` de refresh `observed_at` tren Redis Hash.
   - **KHONG** goi `XADD` len Update Stream.
   - Ham `project()` tra ve `False`.
3. **Behavior khi Semantic State THAY DOI (hoac stream moi)**:
   - Thuc thi atomic pipeline (`MULTI / EXEC`):
     - `HSET` snapshot moi vao Hash.
     - `XADD` SNAPSHOT event vao Stream.
   - Ham `project()` tra ve `True`.

---

## 6. Worker Mismatch Removal Protection

Khi go bo mot stream khoi public transport (`remove()`):
1. Projector doc current snapshot tu Hash bang `HGET`.
2. Neu stream khong ton tai trong Hash -> Tra ve `False` (no-op).
3. Neu stream ton tai, kiem tra truong `worker_id`:
   - Neu snapshot thieu `worker_id`, bi malformed, hoac
     `existing_worker_id != caller_worker_id`: Projector ghi log warning va
     **khong xoa**, tra ve `False`.
   - Neu `existing_worker_id == caller_worker_id`:
     Thuc thi atomic pipeline (`MULTI / EXEC`):
     - `XADD` REMOVED event vao Update Stream.
     - `HDEL` stream field khoi Hash.
     Tra ve `True`.

---

## 7. Thread Lifecycle & Shutdown Coordination

- **Service Loop**: `RuntimeStatusProjectionService` chay tren thread rieng (`runtime-status-projector`).
- **Wake on Demand**: Sau khi command result transaction va `XACK` hoan tat,
  callback `on_command_finalized` kich hoat `projection_service.wake()`. Callback
  co the chay cho ca result thanh cong va terminal failure; viec wake trong
  failure case la harmless va giup refresh observed state.
- **Graceful Shutdown Sequence**:
  1. Set `shutdown_event`.
  2. Kich hoat `projection_service.wake()`.
  3. Join `command_consumer` thread den khi dung han.
  4. Join `projection_thread` den khi dung han.
  5. `supervisor.stop_all()`.
  6. `redis_client.close()`.

Projection loop chay mot final snapshot cycle truoc khi supervisor drain de ghi
nhan observed lifecycle state moi nhat, nhung shutdown worker khong dong nghia
stream bi xoa khoi desired state, nen cycle nay khong phat `REMOVED` hang loat.
Current hash duoc giu lai va backend se danh dau stale khi heartbeat het TTL o
Phase 2. `REMOVED` chi phat khi stream thuc su bien mat khoi supervisor, vi du
sau command `STOP`.

---

## 8. Failure Behavior & Error Isolation

- Projection la **Secondary Public Read Model**, khong duoc phep gay chet Engine / Detection pipeline.
- Neu Redis gap loi (`redis.RedisError` / `RuntimeStatusProjectionError`):
  - Projector bat loi, log warning/error.
  - Khong lam crash `StreamSession`, khong huy hoai FFmpeg process hay pipeline kiem tra black screen / audio loss.
  - Cycle ke tiep se tu dong retry.
- Neu callback `on_command_finalized` gap exception:
  - Consumer log warning, khong rollback command execution, khong anh huong den `XACK` hay ket qua command.
- Neu remove projection gap Redis error, service giu stream trong local tracking
  set va retry remove o cycle sau; no khong quen tombstone sau mot lan loi.

---

## 9. Single-Owner Limitation & Future Multi-Worker

- **Phase 1 Baseline**: Hoat dong toi uu cho mo hinh Single-Worker hoac Sharded-Stream (moi stream chi do mot worker duy nhat so huu).
- **Gioi han**: `HGET` kiem tra `worker_id` roi moi `MULTI / EXEC` giup ngan chan worker go nham status o muc do co ban, nhung chua phai la distributed compare-and-set hoan hao trong moi truong high-concurrency race condition.
- **Dinh huong Multi-Worker**: Phase 10 se bo sung Orchestration Leases va Fencing Generation de bao ve stream ownership tuyet doi o cap ha tang.

`worker_id` va Redis `consumer_name` la hai identity doc lap. `worker_id` den
tu `--worker-id` hoac generated development default; consumer name chi dinh
danh Redis Streams consumer va khong duoc dung lam ownership identity.

---

## 10. Backend Query Examples

### 10.1. Lay toan bo trang thai stream khi khoi dong (Snapshot Initial Load)
```bash
redis-cli HGETALL media-monitor:v1:public:runtime-status
```

### 10.2. Lay trang thai cua 1 stream cu the
```bash
redis-cli HGET media-monitor:v1:public:runtime-status channel-01
```

### 10.3. Doc stream update realtime (WebSocket Bridge)
```bash
# Tao consumer group doc tu vi tri moi nhat ($)
redis-cli XGROUP CREATE media-monitor:v1:public:runtime-status-updates backend-group $ MKSTREAM

# Doc batch update tiep theo
redis-cli XREADGROUP GROUP backend-group backend-01 BLOCK 2000 COUNT 10 STREAMS media-monitor:v1:public:runtime-status-updates >
```

---

## 11. Test Results

Tat ca cac bo test unit, component, contract va Redis integration deu pass:
- **Model Tests**: `test_runtime_status.py`, `test_runtime_status_update.py`
- **Component Tests**: `test_redis_runtime_status_projector.py`, `test_runtime_status_projection_service.py`
- **Redis Integration Tests**: `test_runtime_status_projection.py`
- **Targeted review suite**: `39 passed`
- **Redis integration marker**: `17 passed`
- **Full Regression**: `363 passed, 1 skipped`

---

## 12. Phase 2 Dependency

Phase 2 tiep theo se trien khai **Worker Heartbeat & Discovery Registration Loop**, su dung cac schema va Redis keys da duoc chot trong Phase 0 va Phase 1 de hoan thien toan dien trang thai song (liveness) cua worker.
