# Phase 2 Handoff - Worker Heartbeat & Readiness

## 1. Muc tieu (Objectives)

Phase 2 bo sung co che theo doi trang thai song (liveness), tinh san sang (readiness) va dung luong (capacity) o cap do toan bo **Worker Process**, hoan toan tach biet khoi tinh trang cua tung stream rieng le:

```text
MonitoringWorkerApplication
    |
    +-- StreamSupervisor
    |      -> Quan ly slots & lifecycle tung stream
    |
    +-- RedisMonitoringCommandConsumer
    |      -> Theo doi readiness nhan lenh tu Redis stream
    |
    +-- ObservableProcessGate
    |      -> Theo doi so luong tien trinh FFmpeg dang active
    |
    +-- RuntimeStatusProjectionService
    |      -> Public stream status (Hash & Stream updates)
    |
    +-- WorkerHeartbeatService
           -> Redis Heartbeat Key ({prefix}:workers:{worker_id}:heartbeat) kem TTL
           -> Redis Active Worker Registry ({prefix}:workers:active) ZSET
```

---

## 2. Boundary: Worker Health vs Stream Health

| Khai niem | Worker Heartbeat | Stream Runtime Status |
|---|---|---|
| **Doi tuong dai dien** | Toan bo monitoring worker process | Tung stream/channel rieng biet |
| **Trang thai** | `STARTING`, `READY`, `DEGRADED`, `STOPPING` | `RUNNING`, `PAUSED`, `STOPPED`, `FAILED` |
| **Chi so chat luong** | `command_consumer_ready`, dung luong process/stream | `health` (`HEALTHY`, `DEGRADED`, `UNHEALTHY`), checks |
| **Quy tac doc lap** | Worker van `READY` du co stream bi `UNHEALTHY` (vi worker van hoat dong tot). | Stream `UNHEALTHY` chi mo ta media issue (black screen/audio loss). |
| **Khi Worker mat heartbeat** | Worker bi coi la dead / unavailable; toan bo stream cua worker bi coi la stale. | Stream health khong bi sua thanh UNHEALTHY ma danh dau stale delivery. |

---

## 3. Worker State Semantics

- **`STARTING`**:
  - Worker process vua khoi dong, dang khoi tao cac background components.
  - Consumer group chua duoc tao / chua bat dau poll lenh thanh cong.
- **`READY`**:
  - Worker da san sang van hanh day du: Redis ket noi thanh cong, cac runtime component da start.
  - Neu chay o command worker mode, command consumer da san sang nhan lenh (`is_ready == True`).
  - Neu chay o direct URL mode, worker luon san sang du khong co command consumer.
- **`DEGRADED`**:
  - Worker tung san sang nhung mot thanh phan bat buoc (nhu Redis command consumer) bi mat ket noi hoac gap loi nghiem trong.
  - Khong chuyen sang `DEGRADED` chi vi co stream media alert.
- **`STOPPING`**:
  - Worker da nhan shutdown signal (SIGINT/SIGTERM), tu choi nhan viec moi va thong bao cho backend/orchestrator truoc khi giai phong tai nguyen.
- **Worker Crash (Khong co state `DEAD`)**:
  - Process bi kill ungracefully khong the tu gui event `DEAD`.
  - Backend/Orchestrator coi worker da chet khi heartbeat key het han TTL (15s) hoac `last_seen_at` vuot qua discovery window (30s).

---

## 4. Redis Keys & Transport

| Data | Redis Type | Key Pattern | TTL / Retention | Producer | Consumer |
|---|---|---|---|---|---|
| **Worker Heartbeat** | `STRING` | `media-monitor:v1:workers:{worker_id}:heartbeat` | `EX 15` (15s) | Worker | Backend / Orchestrator |
| **Worker Discovery** | `ZSET` | `media-monitor:v1:workers:active` | Retention 30s | Worker | Backend / Orchestrator |

### Co che Atomic Publishing:
Moi chu ky heartbeat (mac dinh 5s), publisher doc Redis server time, sau do
thuc thi transactional pipeline. Discovery score dung Redis server time thay
vi clock cua worker de mot worker lech clock khong xoa nham worker khac:
```text
TIME -> redis_epoch_timestamp
MULTI
SET media-monitor:v1:workers:{worker_id}:heartbeat <JSON> EX 15
ZADD media-monitor:v1:workers:active <redis_epoch_timestamp> {worker_id}
ZREMRANGEBYSCORE media-monitor:v1:workers:active -inf <redis_epoch_timestamp - 30>
EXEC
```

`last_seen_at` trong payload van la observed time cua worker. ZSET score la
transport/discovery time cua Redis va khong duoc suy dien thanh media time.

---

## 5. Heartbeat Payload Schema

Payload tuan thu tuyet doi `contracts/worker-heartbeat.schema.json`:

```json
{
  "schema_version": "1.0",
  "worker_id": "worker-local-01",
  "state": "READY",
  "started_at": "2026-08-27T09:00:00+00:00",
  "last_seen_at": "2026-08-27T10:00:00+00:00",
  "command_consumer_ready": true,
  "active_stream_count": 2,
  "max_streams": 16,
  "active_media_processes": 3,
  "max_media_processes": 8,
  "version": "dev"
}
```

---

## 6. Theo Doi Dung Luong Thuc Te (Capacity Tracking)

1. **Media Process Capacity (`ObservableProcessGate`)**:
   - Khong truy cap thuoc tinh private `_value` cua semaphore.
   - Su dung mot `Condition` lam chung source of truth cho admission va active counter.
   - Acquire, release va snapshot cung bao ve invariant `0 <= active <= maximum`; khong clamp gia tri sai trong heartbeat.
   - `snapshot()` tra ve `MediaProcessCapacitySnapshot(active=X, maximum=Y)` mot cach an toan va chinh xac.
2. **Stream Capacity (`StreamSupervisor.stream_count()`)**:
   - `active_stream_count` la tong so stream slot dang dang ky trong supervisor (bao gom ca stream `PAUSED` vi chung van chiem slot trong quota `max_streams`).

---

## 7. Command Consumer Readiness & Reconnection

- `RedisMonitoringCommandConsumer` duy tri mot `threading.Event` noi bo qua property `is_ready`.
- Khi `ensure_group()` thanh cong -> `_ready_event.set()`.
- Khi gap `redis.RedisError` khi poll lenh -> `_ready_event.clear()`, dat lai `group_ready = False`.
- Vong lap ke tiep se thuc hien reconnect va goi lai `ensure_group()` truoc khi tiep tuc poll.
- Khi consumer thread ket thuc -> `_ready_event.clear()`.

---

## 8. Startup & Shutdown Orchestration

### Startup Order:
1. Redis ping kiem tra ket noi.
2. Khoi chay background runtime projection thread.
3. Khoi chay background command consumer thread (neu `--command-worker`).
4. Khoi chay background heartbeat thread (phat `STARTING` -> chuyen `READY` khi consumer san sang).

Heartbeat candidate khong thay doi committed worker state. State chi duoc
commit sau khi Redis publish thanh cong; vi vay worker khong the phat
`DEGRADED` neu truoc do chua tung publish `READY` thanh cong.

### Shutdown Order:
1. Nhan signal `SIGINT` / `SIGTERM` -> set `shutdown_event`.
2. Danh thuc projection service va heartbeat service.
3. Heartbeat thread phat state `STOPPING` len Redis voi TTL 15s.
4. Join heartbeat thread va command consumer thread den khi dung han.
5. Join projection thread.
6. `application.close()` (drain stream sessions, dong Redis client).

Redis client khong duoc dong trong khi cac thread su dung chung client con
song. Shutdown deadline va forced termination policy se duoc harden them o
Phase 5; Phase 2 uu tien khong tao race use-after-close.

---

## 9. Huong Dan Backend / Orchestrator Query

### 9.1. Discovery danh sach tat ca worker dang active
```bash
# Lay tat ca worker co score trong khoang [now - 30s, +inf]
redis-cli ZRANGEBYSCORE media-monitor:v1:workers:active <now_epoch - 30> +inf
```

### 9.2. Kiem tra tinh trang song va capacity cua 1 worker
```bash
redis-cli GET media-monitor:v1:workers:worker-local-01:heartbeat
```
- Neu key khong ton tai (da het TTL) -> Worker unavailable / crashed.
- Neu key ton tai -> Doc JSON de biet state (`READY`, `DEGRADED`, `STOPPING`), dung luong stream con lai (`max_streams - active_stream_count`), va dung luong process con lai (`max_media_processes - active_media_processes`).

---

## 10. Test Verification Results

- **Targeted Unit Tests**: Pass 100% (Model validations, ObservableProcessGate concurrency, Redis publisher, Heartbeat service state machine).
- **Redis Integration Tests**: Pass 100% (Real Redis TTL expiration, registry cleanup, stopping state delivery).
- **Full Regression Suite**: 358+ tests pass, zero regressions trong detection pipeline.
