# Phase 4 Handoff - Runtime Status Read Port

## 1. Muc tieu

Phase 4 cung cap read port de backend/UI doc trang thai cua mot stream ma
khong phu thuoc truc tiep vao Redis, supervisor internals, detector, profile
hay FFmpeg.

```text
Backend / REST API
        |
        v
RuntimeStatusReader.get(external_stream_id)
        |
        v
SupervisorRuntimeStatusReader
   |                         |
   v                         v
StreamSupervisor           Redis
(lifecycle/config)         (live telemetry)
```

## 2. Public interface

Port duoc dinh nghia tai `src/core/runtime_status_reader.py`:

```python
class RuntimeStatusReader(Protocol):
    def get(self, stream_id: str) -> RuntimeStatus | None:
        ...
```

- `stream_id` la `external_stream_id`.
- Tra `None` neu stream khong ton tai trong supervisor.
- Tra `RuntimeStatus` neu stream ton tai, ke ca khi Redis unavailable.
- Backend khong duoc tu tinh Redis key hoac truy cap `storage_id`.

## 3. Public DTO

`RuntimeStatus` nam tai `src/models/runtime_status.py` va serialize theo
`contracts/runtime-status.schema.json`.

```json
{
  "schema_version": "1.0",
  "stream_id": "channel-01",
  "status": "RUNNING",
  "health": "HEALTHY",
  "started_at": "2026-08-26T10:00:00+00:00",
  "last_poll_at": "2026-08-26T10:05:00+00:00",
  "active_variant_count": 2,
  "queue_depth": 4,
  "queue_lag_seconds": 1.5,
  "error": null,
  "telemetry_available": true,
  "health_reasons": [],
  "checks": {
    "black_screen": "ENABLED",
    "audio_loss": "DISABLED"
  }
}
```

DTO khong chua `storage_id`, raw Redis key, raw Redis hash hoac internal
session object. Datetime duoc normalize thanh ISO-8601 UTC. `checks` duoc
dong bang khi DTO duoc tao.

`STARTING` ton tai trong public enum de chua duong cho lifecycle sau nay,
nhung runtime hien tai chua sinh trang thai nay.

## 4. Nguon du lieu

| Public field | Source | Quy tac |
|---|---|---|
| `stream_id` | Request/supervisor | Luon la `external_stream_id` |
| `status` | `StreamSessionSnapshot.status` | Lifecycle la source of truth |
| `started_at` | `StreamSessionSnapshot.started_at` | Thoi diem session thuc su start |
| `health` | Lifecycle + Redis health | Chi doc Redis health khi `RUNNING` |
| `health_reasons` | Redis health JSON | Danh sach ly do do health reporter ghi |
| `last_poll_at` | Redis metrics `finished_at` | Thoi diem monitoring cycle gan nhat ket thuc |
| `active_variant_count` | `HLEN` active-variants | So variant dang duoc registry refresh |
| `queue_depth` | Redis metrics | Default `0` neu thieu/khong hop le |
| `queue_lag_seconds` | Redis metrics | `None` neu thieu, am, NaN hoac Infinity |
| `error` | Session snapshot | Chi danh cho lifecycle/session error |
| `checks` | `StreamConfig` | `black_screen` va `audio_loss` enabled state |

`metrics.started_at` khong duoc dung cho public `started_at`, vi do la thoi
diem bat dau mot polling cycle, khong phai thoi diem stream session bat dau.

## 5. Identity boundary

Adapter lay config bang `external_stream_id`, sau do moi resolve
`config.identity.storage_id` de doc ba Redis key noi bo: runtime health,
runtime metrics va active variant registry.

`storage_id` chi duoc dung trong adapter va khong duoc serialize ra public
contract.

## 6. Lifecycle va health mapping

| Session status | Public status | Public health | Doc Redis telemetry |
|---|---|---|---|
| `CREATED` | `CREATED` | `UNKNOWN` | Khong |
| `RUNNING` | `RUNNING` | Theo Redis | Co |
| `PAUSED` | `PAUSED` | `UNKNOWN` | Khong |
| `STOPPING` | `STOPPING` | `UNKNOWN` | Khong |
| `STOPPED` | `STOPPED` | `UNKNOWN` | Khong |
| `FAILED` | `FAILED` | `UNHEALTHY` | Khong |

Telemetry cu trong Redis khong duoc phep lam stream paused/stopped hien
`HEALTHY`.

## 7. Redis behavior

Khi stream `RUNNING`, adapter doc health, metrics va variant count trong mot
pipeline `transaction=False`. Day la mot network batch, khong phai atomic
snapshot giua ba key.

`telemetry_available` co nghia:

- `true`: pipeline da doc Redis thanh cong;
- `false`: Redis unavailable, response pipeline khong hop le, hoac lifecycle
  hien tai khong doc live telemetry.

Khi Redis unavailable, reader van tra lifecycle status, nhung health la
`UNKNOWN`, counters dung safe defaults va Redis error khong bi gan vao
public `error`.

Malformed JSON, timestamp va numeric fields khong lam crash read endpoint.

## 8. File thay doi

- `contracts/runtime-status.schema.json`
- `contracts/PHASE_4_HANDOFF.md`
- `src/models/runtime_status.py`
- `src/core/runtime_status_reader.py`
- `src/core/stream_session.py`
- `src/app/supervisor_runtime_status.py`
- `tests/models/test_runtime_status.py`
- `tests/app/test_supervisor_runtime_status.py`
- `tests/contracts/test_runtime_status_contract.py`

Chi giu mot handoff canonical trong `contracts/`; khong duy tri ban sao trong
`docs/`.

## 9. Verification

- Phase 4 targeted tests: `18 passed`.
- Full regression: `285 passed, 16 skipped`.
- `git diff --check`: pass.

Contract tests kiem tra enum/schema consistency, required public fields va
identity boundary. Hien tai project chua cai JSON Schema validator doc lap,
nen khong mo ta cac test nay la full schema validation.

## 10. Backend Phase 5 usage

```python
status = runtime_status_reader.get(external_stream_id)
if status is None:
    # Map to API 404.
    ...

payload = status.to_dict()
```

Backend chi phu thuoc vao `RuntimeStatusReader` va `RuntimeStatus`. Redis,
supervisor internals va `storage_id` van nam sau adapter boundary.
