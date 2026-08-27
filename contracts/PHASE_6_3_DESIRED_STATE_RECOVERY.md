# Phase 3 Handoff - Desired State & Restart Recovery

## 1. Muc tieu (Objectives)

Phase 3 tach biet ro hai khai niem:
- **Desired State**: Trang thai ma Backend yeu cau stream phai o do (`RUNNING`, `PAUSED`, `STOPPED`).
- **Observed State**: Tinh trang van hanh thuc te ma Worker dang quan sat thay tren stream (`RUNNING`, `PAUSED`, `STOPPED`, `FAILED`).

Vi du: Khi mot stream bi FAILED (do mat mang, nguon HLS chet), Observed State la `FAILED`, nhung Desired State van la `RUNNING`. Worker khong tu y chuyen Desired State thanh `STOPPED`.

Khi Worker bi restart (do deploy, reboot node, hoac crash):
1. Worker tai danh sach Desired States tu Redis Hash.
2. Thuc hien Reconcile dua cac stream ve dung trang thai mong muon (`RUNNING` duoc khoi dong lai, `PAUSED` duoc dang ky ma khong khoi chay session, `STOPPED` khong bi hoi sinh).
3. Sau khi reconcile thanh cong, Command Consumer bat dau poll lenh moi va Heartbeat chuyen sang `READY`.

---

## 2. So Do Kien Truc & Data Flow

```text
Backend Command -> Redis Command Stream
                       |
                       v
         RedisMonitoringCommandConsumer
                       |
                       v
       PersistentMonitoringCommandHandler
          |                        |
          | 1. Lifecycle Op        | 2. Persist Desired State
          v                        v
   MonitoringControl       DesiredStateRepository
          |                        |
          v                        v
   StreamSupervisor        Redis Hash (desired-streams)
          |
          +---- (Worker Restart) <---+
          |                          |
          +--- SupervisorDesiredStateReconciler
```

---

## 3. Data Model & Schema

Model `DesiredStreamState` duoc luu tru duoi dang canonical public JSON trong Redis Hash:

```json
{
  "schema_version": "1.0",
  "stream_id": "channel-01",
  "desired_state": "RUNNING",
  "updated_at": "2026-08-27T10:00:00+00:00",
  "config": {
    "schema_version": "1.0",
    "stream_id": "channel-01",
    "master_url": "https://example.test/live/master.m3u8",
    "checks": {
      "black_screen": {"enabled": true},
      "audio_loss": {
        "enabled": true,
        "threshold_dbfs": -60.0,
        "duration_seconds": 30.0,
        "track_index": 0
      }
    }
  },
  "last_command_id": "cmd-start-001"
}
```

### Nguyen Tac Du Lieu Sach:
- **KHONG** chua `storage_id` (internal hashing cua worker).
- **KHONG** chua `worker_id` (vi stream chua bi rang buoc vinh vien voi 1 worker; worker ownership/fencing thuoc Phase sau).
- **KHONG** chua trang thai FFmpeg hay session object.
- Khi `desired_state == "STOPPED"`, truong `config` la `null` (Tombstone record).
- Domain model khong import Redis adapter hay public mapper. Viec encode/decode
  JSON nam trong `app/desired_state_codec.py` de giu dependency direction
  `app -> models`.

---

## 4. Redis Layout

| Muc dich | Redis Type | Key Pattern | Field / Entry |
|---|---|---|---|
| **Active Desired States** | `HASH` | `media-monitor:v1:monitoring:desired-streams` | `field`: `stream_id`<br>`value`: canonical JSON `DesiredStreamState` |
| **Quarantine Recovery Errors** | `STREAM` | `media-monitor:v1:monitoring:desired-state-recovery-errors` | `entry`: `stream_id`, `payload`, `error`, `quarantined_at` |

---

## 5. Command Persistence Rules & Transaction Boundary

### Mapping Giua Command Va Desired State:
| Command Action | Desired State Sau Khi Xu Ly | Configuration |
|---|---|---|
| `START` | `RUNNING` | Config gui kem command |
| `PAUSE` | `PAUSED` | Giu nguyen config hien tai |
| `RESUME` | `RUNNING` | Giu nguyen config hien tai |
| `UPDATE_CONFIG` | Giu nguyen state hien tai (`RUNNING`/`PAUSED`) | Config moi gui kem command |
| `STOP` | `STOPPED` (Tombstone) | `null` |

### Transaction Boundary:
Thu tu thuc thi bat buoc:
1. **Validate command & schema**.
2. **Apply lifecycle operation** tren in-memory `MonitoringControl`.
3. **Persist desired state** vao Redis Hash thong qua `DesiredStateRepository`.
4. **Finalize result marker + XACK command**.

### Xu Ly Khi Persistence Loi (`DesiredStatePersistenceError`):
Neu lifecycle operation thanh cong nhung viec ghi Desired State vao Redis gap loi:
- **KHONG** XACK command.
- **KHONG** ghi processed marker.
- **KHONG** tra ket qua `FAILED` ve stream ket qua.
- Command duoc giu nguyen trong pending queue cua Redis Consumer Group.
- Khi Redis ket noi lai va command duoc redeliver, lifecycle operation tra ve `NOOP` (idempotent) va tiep tuc ghi Desired State cho den khi thanh cong.
- Trong cung worker process, entry loi va cac entry con lai trong batch duoc
  giu trong deferred queue. Worker khong doc command moi cho den khi desired
  state cua command cu da persist, tranh command cu retry sau ghi de command moi.
- Recovery pending sau khi process bi kill se duoc harden tiep o Phase 5 bang
  `XAUTOCLAIM`/same-consumer pending recovery va kill-window tests.

---

## 6. Restart Recovery & Reconciliation Rules

Khi Worker khoi dong lai trong command-worker mode:
1. **`DesiredLifecycleState.RUNNING`**:
   - Goi `control.start(config)`.
   - Khoi dong lai session, pipeline va FFmpeg process.
2. **`DesiredLifecycleState.PAUSED`**:
   - Goi `supervisor.add(config, start=False)` va `supervisor.pause(stream_id)`.
   - Dang ky slot giu quota, set idle status thanh `PAUSED`.
   - **Tuyet doi khong start roi pause** vi se lam decode segment o giua.
3. **`DesiredLifecycleState.STOPPED`**:
   - Khong tao session hay slot nao tren supervisor (khong bao gio hoi sinh).
4. **Failure Isolation & Quarantine**:
   - Neu 1 record bi corrupt JSON hoac hash field khong khop payload stream ID
     -> transactional `XADD recovery-errors + HDEL corrupt field`, sau do bo qua.
   - Neu 1 record vuot qua quota `max_streams` -> Ghi loi vao recovery report, cac stream truoc do van hoat dong binh thuong.
   - Worker khong bi crash boi bat ky loi stream rieng le nao.

---

## 7. Startup Order & Readiness Flow

1. Worker ping Redis.
2. Worker Heartbeat khoi chay o trang thai `STARTING`.
3. Runtime status projection thread khoi chay.
4. **Reconciler chay khoi phuc toan bo stream tu Redis Hash**.
5. Command Consumer khoi chay, tao consumer group, goi `_ready_event.set()`.
6. Heartbeat nhan thay `consumer.is_ready == True` -> Chuyen sang `READY`.

Neu Redis desired registry tam unavailable, command thread retry reconcile voi
backoff va chua set consumer ready. Worker tiep tuc o `STARTING`, thay vi de
thread chet am tham.

---

## 8. Known Retention Boundary

`STOPPED` hien duoc giu nhu tombstone de restart khong resurrect stream. Redis
registry la recovery state, khong phai history database. Tombstone retention
va cleanup metric phai duoc cau hinh trong deployment/observability phase;
PostgreSQL/backend moi la noi luu business history lau dai.

---

## 9. Test Verification

- **Targeted Tests**: 100% Pass (Model validations, Config mapping, Repository operations, Persistent decorator, Supervisor reconciler, Consumer pending delivery).
- **Redis Integration Tests**: 100% Pass (Recovery stream that, Hash persistence, Quarantine error capture).
- **Full Regression**: 383+ tests pass, giu nguyen 100% pipeline detection black-screen / audio-loss.
