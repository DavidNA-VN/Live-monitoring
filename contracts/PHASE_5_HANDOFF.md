# Phase 5 Handoff - Redis Command Integration

## 1. Muc tieu

Phase 5 noi public monitoring command contract vao engine lifecycle hien tai:

```text
Backend/API
    |
    | XADD public command JSON
    v
Redis command stream
    |
    v
RedisMonitoringCommandConsumer
    |
    v
MonitoringCommandHandler
    |
    v
MonitoringControl (Phase 3)
    |
    v
StreamSupervisor / detection sessions
```

Backend co the doc command outcome tu result stream va runtime snapshot qua
`RuntimeStatusReader` cua Phase 4. Detection logic, policy va profile khong bi
thay doi trong phase nay.

## 2. Public contracts

Input command tiep tuc dung:

- `contracts/monitoring-command.schema.json`
- `contracts/stream-config.schema.json`

Phase 5 bo sung:

- `contracts/monitoring-command-result.schema.json`

Command Redis entry co mot field `payload`, chua JSON dung public command
schema. Result entry cung co field `payload`, chua JSON dung result schema.

Result status:

| Status | Y nghia |
|---|---|
| `APPLIED` | Command tao lifecycle/config transition |
| `NOOP` | Command hop le nhung state da dung nhu yeu cau |
| `REJECTED` | Command/config/identity/state request khong hop le |
| `FAILED` | Monitoring operation khong the hoan thanh |

`changed` chi mo ta transition cua command, khong phai runtime health.

## 3. Redis key ownership

`ControlRedisKeys` so huu control-plane keys:

```text
media-monitor:v1:monitoring:commands
media-monitor:v1:monitoring:command-results
media-monitor:v1:monitoring:command-dead-letter
media-monitor:v1:monitoring:processed:<command-id-hash>
```

Raw `command_id` duoc hash khi dat processed marker de command ID khong tro
thanh mot phan Redis key khong kiem soat.

Control keys khong nam trong black-screen/audio-loss key builders va khong
truyen vao detection domain.

## 4. Command mapping

`stream_config_from_public()` la boundary mapper duy nhat tu public config sang
`StreamConfig`.

Mapper:

- chi chap nhan schema version `1.0`;
- chi expose black-screen va audio-loss fields da chot;
- yeu cau absolute HTTP(S) master URL;
- reject unknown fields, wrong types, NaN va Infinity;
- de internal resource limits va tuning fields dung default cua engine;
- reject neu command `stream_id` khac config `stream_id`.

Backend khong serialize internal `StreamConfig` va khong gui `storage_id`.

## 5. Delivery semantics

Consumer dung Redis Streams consumer group `monitoring-workers`.

- New command duoc doc bang `XREADGROUP`.
- Pending command bi bo lai sau worker restart duoc recover bang `XAUTOCLAIM`.
- Success/failure result, processed marker va `XACK` duoc ghi trong mot Redis
  transaction.
- Neu transaction Redis that bai, entry van pending va co the duoc giao lai.
- Control actions duoc thiet ke idempotent de redelivery sau crash khong tao
  lifecycle transition trung lap.
- Processed marker mac dinh giu 24 gio.
- Result stream mac dinh toi da 10,000 entries.
- DLQ mac dinh toi da 1,000 entries.

Consumer dam bao at-least-once delivery, khong tuyen bo exactly-once execution.
Crash sau lifecycle operation nhung truoc Redis transaction co the lam command
duoc apply lai; `MonitoringControl` se tra `NOOP` neu desired state da dat.

## 6. Command ID idempotency

Processed marker luu fingerprint cua canonical command payload.

- Cung `command_id` va cung payload: ACK retry, khong execute lai, khong publish
  duplicate result.
- Cung `command_id` nhung payload khac: dua vao DLQ voi
  `DUPLICATE_COMMAND_ID`.
- Sau processed TTL, command ID co the duoc xu ly lai. Backend phai dung unique
  command ID va giu retry trong retention window.

## 7. Failure handling

| Failure | Outcome |
|---|---|
| Invalid JSON/schema/config | DLQ hoac `REJECTED`, sau do ACK |
| Not found/conflict/identity mismatch | `REJECTED`, sau do ACK |
| Capacity/operation failure | `FAILED`, sau do ACK |
| Unexpected handler failure | Generic `FAILED` + DLQ, sau do ACK |
| Redis read/write failure | Khong ACK; retry sau |

Unexpected internal exception duoc log day du, nhung public result chi tra
generic message de khong lo infrastructure detail.

## 8. Composition root

`MonitoringWorkerApplication` tao va share dung mot instance cua:

- `StreamSupervisor`;
- `SupervisorMonitoringControl`;
- `SupervisorRuntimeStatusReader`;
- `MonitoringCommandHandler`;
- `RedisMonitoringCommandConsumer`.

Khong tao control adapter moi theo tung request/command, vi per-stream striped
locks cua Phase 3 phai duoc share.

Shutdown chi close Redis sau khi tat ca stream drain thanh cong. Neu drain
timeout, `close()` tra `False` va co the retry.

## 9. CLI modes

Che do URL cu van hoat dong:

```powershell
python src\live_main.py --url "https://example.test/master.m3u8" `
  --stream-id "channel-01"
```

Khoi dong worker de backend gui command qua Redis:

```powershell
python src\live_main.py --command-worker `
  --max-streams 16 `
  --consumer-name "worker-local-01"
```

Co the truyen ca `--url` va `--command-worker`: service start initial stream va
dong thoi tiep tuc consume command.

## 10. MVP deployment assumption

Phase 5 ho tro mot monitoring worker process quan ly toi da `max_streams`.

Khong chay nhieu worker doc chung consumer group trong production MVP. Stream
ownership hien nam trong memory cua tung `StreamSupervisor`; neu nhieu worker
chia command group, PAUSE/RESUME co the duoc giao cho worker khong so huu stream.

Multi-worker production can them stream ownership/routing lease va la phase
scale-out rieng, khong duoc giai quyet bang cach chi tang consumer count.

Redis server can ho tro `XAUTOCLAIM` (Redis 6.2+; deployment khuyen nghi Redis
7+ nhu baseline hien tai).

## 11. Ngoai scope

Phase 5 khong implement:

- REST API endpoints;
- WebSocket gateway;
- UI/dashboard;
- PostgreSQL history;
- multi-worker stream ownership;
- auth, tenant isolation va rate limiting;
- detector moi hoac thay doi black-screen/audio-loss rule.

Backend/UI phai phu thuoc public contracts va application ports, khong import
Redis key builders, detector, profile hay supervisor internals.

## 12. Verification

- Phase 5 targeted unit/contract tests: pass.
- Full regression: `300 passed, 17 skipped`.
- Redis command integration test skip neu disposable Redis offline.
- `git diff --check`: pass.
