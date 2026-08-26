# Phase 3 - Monitoring Control Port

## Trang thai

Phase 3 da dong goi `StreamSupervisor` sau `MonitoringControl`. Backend va Redis
command consumer co the dieu khien lifecycle bang external stream ID ma khong
truy cap session slots, Redis, detector, profile hoac FFmpeg.

## Public application port

`MonitoringControl` expose:

```text
start(config)
pause(stream_id)
resume(stream_id)
stop(stream_id)
update_config(stream_id, config)
```

Moi operation tra `MonitoringControlResult`:

```text
stream_id  external stream ID
action     START/PAUSE/RESUME/STOP/UPDATE_CONFIG
changed    operation co tao state/config transition hay khong
```

Result khong chua storage ID hoac internal session state.

## Stable errors

```text
StreamAlreadyExistsError
StreamNotFoundError
StreamCapacityError
StreamIdentityMismatchError
StreamOperationError
```

Backend map cac error nay sang HTTP/API response; khong parse raw Supervisor
exception messages.

## Lifecycle semantics

```text
START absent                    changed=true
START same config, running      changed=false
START paused/stopped/created    resume, changed=true
START failed                    clean slot and restart
START different config         StreamAlreadyExistsError
START disabled config          StreamOperationError

PAUSE running                  changed=true
PAUSE paused/failed/stopped     changed=false
RESUME paused/created/stopped   changed=true
RESUME running                 changed=false
RESUME failed                  StreamOperationError

STOP existing                  drain/remove, changed=true
STOP missing                   changed=false

UPDATE same config             changed=false
UPDATE changed config          update/restart according to desired state
UPDATE wrong identity          StreamIdentityMismatchError
```

## Concurrency boundary

Control adapter serialize operations theo external stream ID bang fixed striped
locks. Lock pool co kich thuoc co dinh, nen khong tang memory theo tong so stream
ID tung xuat hien. Hai START dong thoi cho cung stream khong the tao hai session
hoac hai FFmpeg assembly.

`SupervisorMonitoringControl` phai duoc tao mot lan cho moi Supervisor/worker va
duoc share boi command path. Phase 5 composition root se giu adapter nay nhu
singleton; khong tao adapter moi cho tung request/command.

## Supervisor additions

Supervisor expose read-only methods:

```text
snapshot(stream_id)
configuration(stream_id)
```

Adapter khong truy cap `_slots`. Supervisor cung co typed internal exceptions
cho duplicate registration, capacity va concurrent start. Cac exception nay van
ke thua ValueError/RuntimeError de giu compatibility voi consumer cu.

## Review findings da xu ly

1. Check-then-act trong adapter co the tao hai session khi factory cham. Da
   them per-stream operation serialization va deterministic slow-factory test.
2. Adapter tung classify ValueError/message co chu `capacity`, co the map nham
   assembly failure thanh conflict/capacity. Da thay bang typed Supervisor
   exceptions.
3. START disabled config tung tra `changed=true` du stream van paused. Hien no
   reject truoc khi register slot.

## Test coverage

- Lifecycle va idempotent no-op cho nam actions.
- Config conflict, identity mismatch, capacity va not-found translation.
- Failed-session recovery va stop drain timeout.
- Same-config update khong restart.
- Paused update giu paused state.
- Concurrent START voi fast va deliberately blocked factory.
- Factory ValueError duoc map thanh operation failure.
- Protocol/action enum match monitoring command schema.
- Static boundary khong co storage ID/detector/profile dependency.

Ket qua review:

```text
Targeted after fixes: 32 passed
Full regression:      267 passed, 16 skipped
```

15 Redis integration tests skip vi Redis local timeout. Mot long-live test skip
vi can opt-in. Khong co test failure.

## Viec backend/UI co the lam sau Phase 3

- Implement fake `MonitoringControl` cung protocol va stable exceptions.
- Map START/PAUSE/RESUME/STOP/UPDATE_CONFIG REST actions sang port.
- Dung `changed` de phan biet applied transition va idempotent no-op.
- Khong dung `changed` nhu runtime health/status.
- Chua import `StreamSupervisor` truc tiep vao backend route.

## Phase tiep theo

Phase 4 tao runtime status mapper/read port. No se map Supervisor snapshot va
Redis runtime health/metrics thanh `RuntimeStatusDTO`, khong expose internal
dataclass hoac raw Redis hash cho backend/UI.
