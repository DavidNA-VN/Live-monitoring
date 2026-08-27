# Phase 6 Handoff - Worker E2E Harness

## 1. Muc tieu (Objectives)

Phase 6 thiet lap he thong kiem thu Hop den (Black-box E2E Test Harness) cho toan bo Monitoring Worker, dong vai tro nhu mot Backend thuc te giao tiep voi Worker hoan toan thong qua cac ranh gioi Public Redis:
- Public Monitoring Commands Stream
- Public Command Results Stream
- Public Current Runtime Status Hash
- Public Status Updates Stream
- Public Worker Heartbeat Key & Active Registry
- Public Alerts Outbox Stream

---

## 2. Cau Truc 3 Tang Kiem Thu (3-Tier Harness)

```text
[Backend Probe]
       |
       +---> (Redis Commands) ---> [MonitoringWorkerRunner]
       |                                   |
       |                                   +--> [StreamSupervisor]
       |                                              |
       |                                (Tier 1/3)    +--> FakeSessionFactory (No FFmpeg)
       |                                (Tier 2)      +--> MonitoringSessionFactory (FFmpeg)
       |
       +<--- (Redis Results / Status / Heartbeat / Alerts) <---+
```

### 1. Tier 1: Fast Lifecycle E2E (`test_worker_lifecycle_e2e.py`)
- Kiem tra toan bo vong doi: `START` -> `RUNNING` -> `Duplicate START (Idempotent)` -> `PAUSE` -> `RESUME` -> `UPDATE_CONFIG` -> `STOP` -> `Clean Shutdown`.
- Su dung `FakeSessionFactory` de chay sieu nhanh trong moi vong build/CI ma khong can FFmpeg hay media stream that.

### 2. Tier 2: Real Media E2E (`test_worker_media_e2e.py`)
- Kiem tra toan tuyen voi video/audio that:
  - Phat HLS local qua `ThreadingHTTPServer` voi port dong (port 0).
  - FFmpeg doc segment, thuc hien pipeline phan tich video (black screen) va audio (audio loss).
  - Alert outbox phat sinh envelope hop le: dung `stream_id`, `event_type`, `alert_id`, va tuyet doi khong chua `storage_id` noi bo.

### 3. Tier 3: Restart Recovery E2E (`test_worker_restart_e2e.py`)
- Kiem tra Worker A khoi dong cac stream `RUNNING`, `PAUSED`, `STOPPED`.
- Worker A bi tat -> Khoi dong Worker B cung namespace.
- Worker B tu dong reconcile khuc phuc dung trang thai mong muon truoc khi san sang nhan lenh moi ma khong can Backend phai gui lai lenh `START`.

---

## 3. Cac Thanh Phan Chinh

| File | Muc dich |
|---|---|
| `tests/e2e/backend_probe.py` | Gia lap Backend: gui lenh, poll ket qua, doi trang thai runtime, doi heartbeat, va doc alert outbox. |
| `tests/e2e/fake_session.py` | Gia lap session stream chay theo dung state machine ma khong can subprocess FFmpeg, dem counter `started_count`, `created_count` de xac nhan khong chay duplicate session. |
| `tests/e2e/conftest.py` | Khoi tao disposable Redis fixture voi unique UUID namespace cho moi test case va tu dong don dep sach se. |
| `scripts/run_worker_e2e.py` | Script chay E2E mot lenh duy nhat voi 3 che do: `fast`, `media`, `all`. |

---

## 4. Huong Dan Chay Test

### 1. Fast Mode (Chay trong CI / Regression thuong xuyen)
```powershell
python scripts/run_worker_e2e.py fast
# Hoac truc tiep:
.\.venv\Scripts\python.exe -m pytest tests/e2e -m "worker_e2e and not media_e2e" -q
```

### 2. Real Media Mode (Yeu cau FFmpeg trong PATH va Redis)
```powershell
python scripts/run_worker_e2e.py media
# Hoac:
$env:RUN_WORKER_MEDIA_E2E="1"; .\.venv\Scripts\python.exe -m pytest tests/e2e -m "media_e2e" -q
```

### 3. All Mode (Chay toan bo E2E)
```powershell
python scripts/run_worker_e2e.py all
```

---

## 5. Ket Qua Kiem Thu (Verification)

- Fast Lifecycle E2E: **PASS**
- Restart Recovery E2E: **PASS**
- Real Media E2E: **PASS** (khi co FFmpeg) / **SKIPPED an toan** (khi khong bat co `RUN_WORKER_MEDIA_E2E=1`)
- Duplicate command duoc replay voi cung payload, duoc ACK va chi tao mot command result/session.
- Moi runner, publisher va HTTP server phai dung hoan toan truoc khi fixture Redis/media duoc don dep.
- So luong regression test can duoc ghi theo ket qua CI cua commit thay vi dong cung trong tai lieu nay.

Ket qua review tai Phase 6:

- Fast E2E: `3 passed`.
- Real-media E2E: `2 passed`.
- Full regression: `423 passed, 3 skipped`.
- `git diff --check`: pass; canh bao LF/CRLF tren Windows khong phai whitespace error.

---

## 6. Quyet Dinh Kien Truc

- `MonitoringWorkerApplication` chi them `session_factory` injection seam. Production van mac dinh dung `MonitoringSessionFactory`; test khong chen nhanh re nhanh vao detector, scheduler hay Redis repository.
- Fast tier dung fake session de kiem tra worker orchestration. Tier nay khong duoc xem la bang chung FFmpeg/detector hoat dong dung.
- Media tier phai di qua command stream, worker runner, live HLS, FFmpeg profile va public alert outbox. Khong assert truc tiep internal event store.
- Redis test dung namespace UUID rieng va chi xoa key trong namespace do; khong dung `FLUSHDB`.
- `src/checks/__init__.py` la package boundary can thiet de Python khong resolve nham `tests/checks` khi pytest collection.
- `publish_live_hls.publish()` ho tro `stop_event` de harness dung publisher co kiem soat; CLI behavior va gia tri mac dinh production khong thay doi.

## 7. Ngoai Pham Vi Phase 6

- Khong benchmark so stream toi da hay sizing CPU/GPU.
- Khong kiem tra failover giua nhieu worker chay dong thoi hoac distributed ownership/lease.
- Khong thay the unit, Redis integration hay detector rule tests bang E2E.
- Khong dua Backend/UI/WebSocket implementation vao worker repository.
- Khong mo rong them detection case moi.

## 8. Dieu Kien Dong Phase

- Duplicate delivery cung `command_id` va cung payload chi tao mot result va mot session.
- Restart phuc hoi dung `RUNNING`/`PAUSED`, khong phuc sinh `STOPPED`.
- Pending command duoc worker moi claim va xu ly mot lan.
- Black-screen va audio-loss deu tao alert qua public outbox voi external `stream_id`, khong lo `storage_id`.
- Tat ca background thread duoc join truoc khi cleanup Redis va temporary media.
- Fast E2E va full regression chay duoc khi media E2E khong duoc opt-in; media E2E skip co chu dich khi thieu prerequisite.
