# Phase 0 Integration Contract - Worker, Backend, UI & Orchestrator

## 1. Muc tieu

Phase 0 xac lap va dong bang toan bo public contract giua cac thanh phan:

```text
+-------------------+        +--------------------+        +-------------------+
| Monitoring Worker | <----> |   Redis Transport  | <----> |   Backend / API   |
| (Stream Engine)   |        | (Hash/Stream/Zset) |        | (Status / Control)|
+-------------------+        +--------------------+        +-------------------+
                                                                     ^
                                                                     | WebSocket / REST
                                                                     v
                                                           +-------------------+
                                                           |   UI / Dashboard  |
                                                           +-------------------+
```

Muc tieu cot loi:
- **Demo worker don gian**: Chay 1 worker process doc lap co dinh danh ro rang.
- **Worker Identity**: Moi payload quan sat deu mang `worker_id` chuan hoa.
- **Tach biet Backend & Engine**: Backend doc va project runtime status truc tiep tu Redis ma khong can import Python code cua worker/detector.
- **San sang cho Multi-Worker & Orchestrator**: Schema duoc thiet ke de khi mo rong nhieu worker trong tuong lai khong lam thay doi major version cua contract.
- **Bao toan Engine Internals**: Khong dua distributed state (lease, fencing token, internal queue, storage_id) vao detector hoac public schemas.
- **Pham vi Phase 0**: Hoan toan tap trung vao Schema design, Transport specification va Contract verification tests (chua implement publisher/heartbeat loop vao `src/`).

---

## 2. So do worker/backend

```text
[Control Plane - Ingress]
Backend API ---> XADD media-monitor:v1:monitoring:commands ---> Redis Command Stream
                                                                        |
                                                                        v
                                                          Monitoring Worker (worker-local-01)
                                                          - RedisMonitoringCommandConsumer
                                                          - MonitoringCommandHandler
                                                          - StreamSupervisor / Live Sessions
                                                                        |
[Data Plane / Status Projection]                                       v
- Status Hash:   HSET media-monitor:v1:public:runtime-status <stream_id> <RuntimeStatus>
- Status Stream: XADD media-monitor:v1:public:runtime-status-updates payload <RuntimeStatusUpdate>
- Heartbeat:     SET  media-monitor:v1:workers:<worker_id>:heartbeat <Heartbeat> EX 15
- Discovery:     ZADD media-monitor:v1:workers:active <epoch> <worker_id>
- Results:       XADD media-monitor:v1:monitoring:command-results payload <Result>
- Alerts:        XADD media-monitor:v1:alerts:outbox payload <Alert>
                                                                        |
                                                                        v
Backend Ingestion Service <-------------------------------- Redis Data Channels
  |
  +--> WebSocket Gateway ---> UI Live Dashboard (Streams, Health, Alerts, Worker Status)
  +--> REST API Query    ---> Client inspection (Initial snapshot via HGETALL)
```

---

## 3. Redis channels

| Du lieu | Redis Type | Key | TTL / Retention | Producer | Consumer |
|---|---|---|---|---|---|
| **Current Statuses** | `HASH` | `media-monitor:v1:public:runtime-status` | Persistent (HDEL on STOP) | Worker | Backend |
| **Status Updates** | `STREAM` | `media-monitor:v1:public:runtime-status-updates` | `MAXLEN ~ 10000` | Worker | Backend / WebSocket |
| **Worker Heartbeat** | `STRING` | `media-monitor:v1:workers:{worker_id}:heartbeat` | `EX 15` (15s) | Worker | Backend / Orchestrator |
| **Worker Discovery** | `ZSET` | `media-monitor:v1:workers:active` | Persistent Score Window | Worker | Backend / Orchestrator |
| **Commands** | `STREAM` | `media-monitor:v1:monitoring:commands` | `MAXLEN ~ 10000` | Backend | Worker |
| **Command Results** | `STREAM` | `media-monitor:v1:monitoring:command-results` | `MAXLEN ~ 10000` | Worker | Backend |
| **Alerts** | `STREAM` | `media-monitor:v1:alerts:outbox` | `MAXLEN 10000` default | Worker | Backend / Notifier |

### Quy tac serialization va key convention:
1. **Current status hash**:
   - `HSET media-monitor:v1:public:runtime-status <stream_id> <RuntimeStatus JSON string>`
   - Key field la `stream_id` (public external identity).
2. **Status update stream**:
   - `XADD media-monitor:v1:public:runtime-status-updates MAXLEN ~ 10000 * payload <RuntimeStatusUpdate JSON string>`
3. **Worker heartbeat**:
   - `SET media-monitor:v1:workers:{worker_id}:heartbeat <WorkerHeartbeat JSON string> EX 15`
   - `worker_id` nam trong key vi do deployment config/pod identity kiem soat va da validate theo regex.
4. **Worker discovery**:
   - `ZADD media-monitor:v1:workers:active <epoch_timestamp_seconds> {worker_id}`
   - Backend/orchestrator chi coi worker active khi:
     1. Nam trong discovery window (`now - 30s <= score`).
     2. Heartbeat key `media-monitor:v1:workers:{worker_id}:heartbeat` ton tai.
     3. Payload `last_seen_at` khong bi stale.

### Serialization boundary

- Command, command-result va runtime-status-update entries dung mot field
  `payload` chua JSON theo public schema tuong ung.
- Alert outbox hien dung cac Redis Stream fields phang do
  `AlertEnvelope.to_redis_fields()` tao ra; alert entry khong boc trong field
  `payload`.
- UI khong ket noi Redis truc tiep. Backend la consumer cua public Redis plane
  va dua DTO len UI qua REST/WebSocket.

---

## 4. Payload examples

### 4.1. Runtime Status (`contracts/runtime-status.schema.json`)
Luu trong Redis Hash `media-monitor:v1:public:runtime-status`:

```json
{
  "schema_version": "1.0",
  "stream_id": "channel-01",
  "status": "RUNNING",
  "health": "HEALTHY",
  "started_at": "2026-08-27T09:00:00+00:00",
  "last_poll_at": "2026-08-27T10:00:00+00:00",
  "active_variant_count": 2,
  "queue_depth": 0,
  "queue_lag_seconds": 0.0,
  "error": null,
  "telemetry_available": true,
  "health_reasons": [],
  "checks": {
    "black_screen": "ENABLED",
    "audio_loss": "ENABLED"
  },
  "worker_id": "worker-local-01",
  "observed_at": "2026-08-27T10:00:02+00:00"
}
```

### 4.2. Runtime Status Update - SNAPSHOT (`contracts/runtime-status-update.schema.json`)
Gui len Redis Stream `media-monitor:v1:public:runtime-status-updates`:

```json
{
  "schema_version": "1.0",
  "update_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "update_type": "SNAPSHOT",
  "stream_id": "channel-01",
  "worker_id": "worker-local-01",
  "observed_at": "2026-08-27T10:00:02+00:00",
  "status": {
    "schema_version": "1.0",
    "stream_id": "channel-01",
    "status": "RUNNING",
    "health": "HEALTHY",
    "started_at": "2026-08-27T09:00:00+00:00",
    "last_poll_at": "2026-08-27T10:00:00+00:00",
    "active_variant_count": 2,
    "queue_depth": 0,
    "queue_lag_seconds": 0.0,
    "error": null,
    "telemetry_available": true,
    "health_reasons": [],
    "checks": {
      "black_screen": "ENABLED",
      "audio_loss": "ENABLED"
    },
    "worker_id": "worker-local-01",
    "observed_at": "2026-08-27T10:00:02+00:00"
  }
}
```

### 4.3. Runtime Status Update - REMOVED (`contracts/runtime-status-update.schema.json`)
Khi stream bi STOP hoac xoa bo:

```json
{
  "schema_version": "1.0",
  "update_id": "c3a7f43e-110d-45de-9856-4c74f51e06d9",
  "update_type": "REMOVED",
  "stream_id": "channel-01",
  "worker_id": "worker-local-01",
  "observed_at": "2026-08-27T10:15:00+00:00"
}
```

### 4.4. Worker Heartbeat (`contracts/worker-heartbeat.schema.json`)
Luu vao Redis String `media-monitor:v1:workers:{worker_id}:heartbeat`:

```json
{
  "schema_version": "1.0",
  "worker_id": "worker-local-01",
  "state": "READY",
  "started_at": "2026-08-27T09:00:00+00:00",
  "last_seen_at": "2026-08-27T10:00:00+00:00",
  "command_consumer_ready": true,
  "active_stream_count": 1,
  "max_streams": 4,
  "active_media_processes": 2,
  "max_media_processes": 4,
  "version": "mvp-monitoring-v0.1.0"
}
```

---

## 5. Lifecycle semantics

| Action / Event | Command Result | Current Hash (`HSET`/`HDEL`) | Update Stream (`XADD`) |
|---|---|---|---|
| **START / RESUME** | `APPLIED` / `NOOP` | `HSET <stream_id> <RuntimeStatus>` | `SNAPSHOT` |
| **PAUSE** | `APPLIED` / `NOOP` | `HSET <stream_id> <RuntimeStatus (PAUSED)>` | `SNAPSHOT` |
| **UPDATE_CONFIG** | `APPLIED` / `NOOP` | `HSET <stream_id> <RuntimeStatus (new checks)>` | `SNAPSHOT` |
| **STOP** | `APPLIED` / `NOOP` | `HDEL <stream_id>` (trong atomic transaction voi stream) | `REMOVED` |
| **Worker Crash** | N/A | **Khong tu HDEL**. Status cu giu nguyen; Heartbeat TTL het han; Backend suy ra stale. | N/A |

### Nguyen tac atomicity (Phase 1):
Khi thuc hien `STOP`, worker se dong thoi phat sinh event `REMOVED` len stream va xoa khoi hash `HDEL` trong cung mot Redis pipeline / multi-exec transaction.

### Worker crash behavior:
Worker bi kill ungracefully se khong kip gui `REMOVED` hay `HDEL`. Trang thai cu trong hash duoc giu nguyen de backend ghi nhan snapshot cuoi cung va danh dau `stale` sau khi heartbeat key het han (15 giay), giup phuc vu post-mortem debugging ma khong lam mat dau vet loi.

Schema bat buoc `SNAPSHOT` co `status`, va cam `REMOVED` mang theo `status`.
Projected `status` ben trong `SNAPSHOT` bat buoc co `worker_id` va
`observed_at`. Viec envelope/status co cung `stream_id`, `worker_id` va
`observed_at` la semantic invariant ma producer Phase 1 phai enforce va test;
JSON Schema 2020-12 khong tu so sanh gia tri giua hai vi tri nay.

---

## 6. Heartbeat & stale rules

### Thong so mac dinh (co the cau hinh):
- **Heartbeat refresh interval**: `5s`
- **Heartbeat key TTL**: `15s` (`EX 15`)
- **Status projection interval**: `2s`
- **Status stale threshold**: `15s`
- **Status update stream max length**: `10,000`

### Suy luan trang thai phia Backend / UI:
1. **Worker unavailable**: Khong tim thay key `media-monitor:v1:workers:{worker_id}:heartbeat` hoac khong co score hop le trong ZSET `media-monitor:v1:workers:active`.
2. **Stream status stale**: Neu `observed_at` cua stream status cach hien tai `> 15s` (hoac worker phu trach stream do bi unavailable).
3. **Phan biet Freshness vs Detector Health**:
   - `stale` la trang thai ve **tinh tuoi moi cua delivery** (delivery freshness).
   - `health` (`HEALTHY`, `DEGRADED`, `UNHEALTHY`) la trang thai **chat luong stream do detector tinh toan**.
   - **Khong duoc tu dong sua `health=HEALTHY` thanh `health=UNHEALTHY` chi vi status bi stale.** Backend/UI phai hien thi ro badge `STALE` ben canh detector `health`.

### Y nghia timestamp:
- `started_at`: Thoi diem bat dau monitoring session cua stream.
- `last_poll_at`: Thoi diem monitoring cycle (polling/variant check) gan nhat ket thuc.
- `observed_at`: Thoi diem public snapshot nay duoc tao va emit ra public plane.

---

## 7. Single-worker demo architecture

Trong che do single-worker MVP / Demo:
- Worker process se co dinh danh rieng qua config Phase 2, du kien
  `--worker-id worker-local-01` hoac `WORKER_ID=worker-local-01`.
- `consumer_name` cua Redis consumer khong duoc mac dinh xem la `worker_id`;
  hai gia tri co lifecycle va ownership semantics khac nhau.
- Worker dang ky consumer group va doc command tu `media-monitor:v1:monitoring:commands`.
- Toan bo stream khoi tao boi worker nay duoc quan ly boi mot `StreamSupervisor` duy nhat trong bo nho cua process do.
- Worker dinh ky phat heartbeat vao `media-monitor:v1:workers:{worker_id}:heartbeat` va project status vao Hash / Stream.

---

## 8. Multi-worker production direction

### Gioi han cua naive multi-worker:
Khong cho phep nhieu worker doc chung mot consumer group ngau nhien tu global command stream, vi `StreamSupervisor` quan ly state in-memory theo process. Neu mot worker doc phai lenh `PAUSE channel-01` ma stream do do worker khac quan ly thi se bao loi `REJECTED (STREAM_NOT_FOUND)`.

### Kien truc Multi-Worker tiep theo (Orchestrator Pattern):
```text
Global Command Ingress (API)
            |
            v
Orchestrator / Shard Manager
            | (Tra cuu worker dang giu stream / chon worker co capacity phu hop)
            v
Worker-Specific Route (Command Stream / Worker Group)
            |
            v
Monitoring Worker (assigned worker_id)
```

### Chuan bi trong Phase 0:
- Schema da co san `worker_id` de Backend biet worker nao dang quan sat stream.
- Heartbeat co san metrics `active_stream_count`, `max_streams`, `active_media_processes`, `max_media_processes` de Orchestrator lua chon capacity.
- Desired state (yeu cau tu API) duoc tach biet hoan toan khoi Observed status (ket qua quan sat cua worker).

Heartbeat producer phai bao dam semantic invariant:

```text
active_stream_count <= max_streams
active_media_processes <= max_media_processes
started_at <= last_seen_at
```

Day la cross-field validation do producer/tests enforce; JSON Schema hien tai
chi validate tung field la integer va khong am.

---

## 9. Public vs internal boundary

Cac public contracts tuyet doi tuan thu quy tac cach ly:

| Thong tin | Co trong Public Schema? | Ly do |
|---|---|---|
| `stream_id` | **Co** | Dinh danh duy nhat cua channel ngoai he thong |
| `worker_id` | **Co** | Dinh danh instance quan sat stream |
| `observed_at` | **Co** | Timestamp do freshness |
| `checks` | **Co** | Cau hinh va trang thai cac bo kiem tra public |
| `storage_id` | **KHONG** | Internal storage identifier cua engine |
| Redis Key Names | **KHONG** | Transport implementation detail |
| `ownership_token` / `lease_token` | **KHONG** | Internal orchestration state |
| `fencing_generation` | **KHONG** | Internal consensus state |
| Queue / Process Object | **KHONG** | In-memory runtime objects |

---

## 10. Backend mock instructions

Doi ngu Backend co the trien khai mock hoan toan doc lap dua tren cac contract nay ma khong can chay engine:

### 10.1. Mock Stream Status Hash (Initial Load / REST Query)
Ghi du lieu vao Redis Hash:
```bash
redis-cli HSET media-monitor:v1:public:runtime-status channel-01 '{"schema_version":"1.0","stream_id":"channel-01","status":"RUNNING","health":"HEALTHY","started_at":"2026-08-27T09:00:00+00:00","last_poll_at":"2026-08-27T10:00:00+00:00","active_variant_count":2,"queue_depth":0,"queue_lag_seconds":0.0,"error":null,"telemetry_available":true,"health_reasons":[],"checks":{"black_screen":"ENABLED","audio_loss":"ENABLED"},"worker_id":"worker-local-01","observed_at":"2026-08-27T10:00:00+00:00"}'
```

### 10.2. Mock Live Status Stream (WebSocket Feed)
Phat event cap nhat trang thai:
```bash
redis-cli XADD media-monitor:v1:public:runtime-status-updates * payload '{"schema_version":"1.0","update_id":"test-update-01","update_type":"SNAPSHOT","stream_id":"channel-01","worker_id":"worker-local-01","observed_at":"2026-08-27T10:00:00+00:00","status":{"schema_version":"1.0","stream_id":"channel-01","status":"RUNNING","health":"HEALTHY","started_at":"2026-08-27T09:00:00+00:00","last_poll_at":"2026-08-27T10:00:00+00:00","active_variant_count":2,"queue_depth":0,"queue_lag_seconds":0.0,"error":null,"telemetry_available":true,"health_reasons":[],"checks":{"black_screen":"ENABLED","audio_loss":"ENABLED"},"worker_id":"worker-local-01","observed_at":"2026-08-27T10:00:00+00:00"}}'
```

### 10.3. Mock Worker Heartbeat & Discovery
```bash
# Phase 2 heartbeat string with 15s TTL. Refresh timestamps when running this mock.
redis-cli SET media-monitor:v1:workers:worker-local-01:heartbeat '{"schema_version":"1.0","worker_id":"worker-local-01","state":"READY","started_at":"2026-08-27T09:00:00+00:00","last_seen_at":"2026-08-27T10:00:00+00:00","command_consumer_ready":true,"active_stream_count":1,"max_streams":4,"active_media_processes":2,"max_media_processes":4,"version":"mvp-monitoring-v0.1.0"}' EX 15

# Discovery ZSET
redis-cli ZADD media-monitor:v1:workers:active 1787834400 worker-local-01
```

---

## 11. Ngoai scope (Out of scope Phase 0)

Phase 0 tap trung vao contract boundary va dac ta kien truc, khong bao gom:
- Code publisher/projector vao `src/app/` hoac `src/core/`.
- Code heartbeat background task / thread.
- Desired-state recovery loop.
- Dynamic orchestrator va auto-rebalancing.
- Ownership lease / fencing token management.
- REST API / WebSocket Gateway server implementation.
- PostgreSQL storage schema.
- Thuat toan detector moi hoac thay doi logic black screen / audio loss.

---

## 12. Verification & Test results

- **Contract Unit Tests**: `23 passed`; kiem tra schema structure, enum, conditional rules,
  backward compatibility, sample consistency va public/internal isolation.
- **JSON Schema validation**: Project chua cai independent JSON Schema
  validator; khong tuyen bo day la full standards-based payload validation.
- **Full Regression Test Suite**: `331 passed, 1 skipped`.
- **Redis Integration Tests**: `16 passed, 316 deselected` (xac nhan
  khong lam anh huong den command/control va Redis event pipeline hien tai).
- **Source Hygiene**: `src/` hoan toan khong co diff trong git.
