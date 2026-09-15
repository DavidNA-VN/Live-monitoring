# Phase Realtime 1 - Pending And In-Flight Queue Semantics

## Goal

Admission pressure chi duoc tinh tu profile-work dang cho. Work da duoc
executor nhan va dang xu ly van duoc giu de retry/claim bookkeeping, nhung khong
duoc lam scheduler hieu nham rang pending backlog dang tang.

Phase nay khong thay batch size, khong song song hoa segment analysis va khong
thay detector/reducer/business rule.

## Queue states

`SegmentAdmissionQueue` co hai trang thai cua retained work:

```text
PENDING   = nam trong queue va chua duoc protect
IN_FLIGHT = nam trong queue va da duoc protect boi submitted batch
```

Invariant tai mot atomic pressure snapshot:

```text
retained_depth = pending_depth + in_flight_depth
```

`pressure_snapshot()` lay tat ca count va age duoi cung mot lock:

- `pending_depth`;
- `in_flight_depth`;
- `retained_depth`;
- `oldest_pending_age_seconds`;
- `oldest_retained_age_seconds`.

Properties `depth` va `oldest_age_seconds` cu van ton tai va mang nghia total
retained de tuong thich. Admission controller va runtime queue pressure chi dung
pending values.

## Runtime telemetry

Redis runtime metrics va public status bo sung:

```text
pending_work_count
in_flight_work_count
retained_work_count
```

`queue_depth` la backward-compatible alias cua `pending_work_count`.
`queue_lag_seconds` la tuoi cua pending item cu nhat. Public fields moi la
optional/default zero trong schema version `1.0`, nen snapshot cu van doc duoc.

Dashboard hien:

- `Pending Lag` thay cho nhan `Queue Lag`;
- `Pending / In Flight` de operator thay backlog va work dang chay rieng.

## Safety behavior

- Expiry va capacity eviction chi xoa pending work.
- Live-edge protection chi xoa pending work.
- `protect()` bo qua identity khong con trong queue.
- `acknowledge()` xoa ca retained item va in-flight membership.
- Controller khong chuyen mode neu pending rong, ke ca in-flight item da chay
  lau hon hard lag threshold.

## Definition of Done

- pending/in-flight/retained snapshot atomic va thread-safe;
- controller dung oldest pending age;
- runtime metrics, public DTO, strict codec va JSON schema backward-compatible;
- Dashboard phan biet pending voi in-flight;
- queue, scheduler, status va contract tests pass;
- full regression va production-path benchmark smoke pass;
- detection result khong thay doi.

