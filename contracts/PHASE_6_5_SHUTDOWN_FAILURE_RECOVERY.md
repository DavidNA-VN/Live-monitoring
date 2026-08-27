# Phase 5 Handoff - Shutdown Correctness & Crash Recovery

## 1. Muc tieu (Objectives)

Phase 5 thiet lap do tin cay cao nhat cho vong doi van hanh va tat ung dung cua Worker:
- **Khong mat command khong dau vet**: Bat ky command nao da gui vao Redis deu duoc luu pending neu chua hoan tat day du ca lifecycle, desired state va result transaction.
- **Khong tao ket qua FAILED sai khi Redis gap su co**: Loi Redis trong luc finalize ket qua khong bi coi la loi stream logic va khong bi day vao DLQ.
- **Pending commands luon duoc uu tien**: Worker luon xu ly het command pending cu truoc khi nhan command moi.
- **Dung hoat dong an toan (Graceful Shutdown)**: Khi nhan SIGINT/SIGTERM, Worker lap tuc chuyen Heartbeat sang `STOPPING`, ngung nhan viec moi, cho command dang xu ly ket thuc, dung stream sessions, thuc hien projection cuoi cung va dong ket noi Redis o buoc cuoi cung.

---

## 2. Crash Windows & Expected Recovery

| Crash Window | Mo ta thoi diem crash | Hanh vi sau khi Worker Restart / Reconnect |
|---|---|---|
| **Window A** | Truoc khi goi lifecycle | Command van o pending; worker doc lai va chay binh thuong. |
| **Window B** | Lifecycle thanh cong, truoc khi ghi Desired State | Control chay idempotent (tra `changed=False`), Desired State duoc ghi vao Redis Hash. |
| **Window C** | Desired State da ghi, truoc khi commit Result Transaction | Control tra NOOP, Desired State giu nguyen, Result duoc commit + XACK. |
| **Window D** | Dang commit Result Transaction trong Redis | MULTI/EXEC la atomic: hoac commit toan bo (marker + result + ACK), hoac command van pending. |
| **Window E** | Result + Processed Marker + XACK da commit | Processed marker ngan execute duplicate session, ACK xac nhan hoan tat. |

---

## 3. Xu Ly Loi Redis Trong `_finalize()`

Trong `RedisMonitoringCommandConsumer._process()`:
```python
except redis.RedisError:
    # Re-raise de run() xu ly: clear readiness va retry sau khi reconnect
    raise
```
Khi Redis gap su co trong luc `pipeline.execute()` tai `_finalize()`:
- **KHONG** tao `MonitoringCommandResult` voi status `FAILED`.
- **KHONG** ghi command vao Dead-Letter Queue (`command-dead-letter`).
- **KHONG** `XACK` command nguon.
- Clear `_ready_event` de Heartbeat biet consumer chua san sang.
- Giu command trong pending stream de tu dong retry sau khi Redis on dinh tro lai.

---

## 4. Thu Tu Pending Recovery (Priority Order)

Khi goi `poll_once()`, worker tuyet doi khong doc command moi neu con command pending:

1. **Deferred Entries**: Cac entry bi hoan trong bo nho (memory).
2. **Own Pending Entries**: `XREADGROUP group consumer {commands: "0"} count=batch_size`.
3. **Abandoned Pending Entries**: `XAUTOCLAIM commands group consumer idle_ms "0-0" count=batch_size`.
4. **New Entries**: `XREADGROUP group consumer {commands: ">"} count=batch_size block=block_ms`.

Neu `XPENDING` van bao co entry nhung chua entry nao du tuoi `XAUTOCLAIM`,
worker dat pending barrier va chua doc command moi. Rule nay danh cho single-worker
MVP; routing theo owner trong multi-worker thuoc Phase 10.

---

## 5. STOPPING Admission Rule

Khi nhan tin hieu dung (`stop_event.is_set()`):
1. Clear consumer readiness ngay lap tuc.
2. Khong tiep tuc fetch batch moi tu Redis (`poll_once` tra ve `0`).
3. Neu dang xu ly 1 entry trong batch: entry do duoc thuc thi tron ven (lifecycle -> desired persistence -> result transaction).
4. Cac entry con lai trong batch chua bat dau se duoc giu nguyen o trang thai pending trong Redis.
5. Command thread ket thuc an toan.

---

## 6. Shutdown Coordinator (`MonitoringWorkerRunner`)

Dieu phoi qua trinh shutdown tuan tu voi deadline tong (`shutdown_timeout`):

```text
[SIGINT/SIGTERM / Failure Event]
              |
              v
1. Heartbeat -> latch + publish STOPPING
              |
              v
2. Command Consumer -> Stop fetching, drain in-flight command, join thread
              |
              v
3. StreamSupervisor -> stop_all(timeout=remaining_time)
              |
              v
4. Final Projection -> wake & join projection thread
              |
              v
5. Heartbeat -> join heartbeat thread
              |
              v
6. Redis Client -> close() [chi khi command, stream, projection va heartbeat da dung]
```

STOPPING la latch: heartbeat dinh ky khong the ghi `READY` de len `STOPPING`
trong luc command/stream dang drain. Neu bat ky thread hoac stream session nao
con song sau deadline, Redis duoc giu mo va report `redis_closed=false`; caller
co the retry shutdown, container manager moi quyet dinh force termination.

### Thread Failure Propagation:
Neu bat ky background thread nao (command consumer, heartbeat, projection) bi crash do exception khong mong muon:
- Runner bat exception qua `_run_service`.
- Ghi nhan loi vao danh sach failure kem service name va worker ID.
- Kich hoat `request_shutdown()` de toan bo worker ha canh an toan thay vi tiep tuc chay o trang thai hong.

---

## 7. Stream Drain Contract & Retry Semantics

- Neu `StreamSession.stop()` timeout: Session van duoc giu trong slot supervisor de co the goi `stop_all()` retry tiep.
- Timeout truyen vao `stop_all()` la deadline tong cho tat ca session, khong
  duoc nhan len theo so stream.
- `StreamSession.close_callbacks`: Duoc bao ve boi `Lock` va `_closed` flag, dam bao chi thuc thi duy nhat 1 lan.
- Trạng thái mong muốn (Desired State) trong Redis **khong bi sua** thanh `STOPPED` khi shutdown worker vi day la worker deployment shutdown, khong phai user STOP command.

---

## 8. Ket Qua Kiem Tra (Verification Checklist)

- [x] Redis loi trong `_finalize()` khong lam sinh ket qua FAILED va khong ACK.
- [x] Command pending cu duoc uu tien xu ly truoc command moi.
- [x] Orphan pending chua du tuoi claim chan delivery command moi.
- [x] `XAUTOCLAIM` phuc hoi abandoned pending tu worker cu da chet.
- [x] Worker STOPPING khong nhan them viec va drain dung command dang chay.
- [x] Shutdown report ghi nhan day du cac truong (`commands_drained`, `streams_drained`, `redis_closed`, `timed_out`, `errors`).
- [x] Thread failure duoc propagate len runner de kich hoat shutdown.
- [x] Redis chi dong sau khi background threads va stream sessions da ket thuc;
  shutdown chua xong co the retry.
- [x] Full regression test bo sung pass 100% (405+ tests).
