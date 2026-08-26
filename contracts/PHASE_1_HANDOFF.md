# Phase 1 - Stream Identity Boundary

## Trang thai

Phase 1 da hoan thanh boundary giua public stream identity va internal storage
identity ma khong thay doi black-screen/audio-loss detection behavior.

## Identity model

`StreamIdentity` hien co ba field:

```text
external_stream_id  ID public cua channel/stream
storage_id          SHA-256 rut gon 24 ky tu dung noi bo
master_url          Live HLS master URL
```

Khi API/CLI cung cap `stream_id`, engine trim gia tri, giu no lam
`external_stream_id`, va hash no thanh `storage_id`. ID rong hoac dai hon 128
ky tu bi reject de match `stream-config.schema.json`.

Khi khong co `stream_id`, URL fingerprint duoc dung cho ca external va storage
identity. Cac hash hien tai tu explicit ID va URL fallback van match version cu,
nen Redis keyspace khong bi thay doi boi Phase 1.

## Boundary da ap dung

Public/control boundary dung `external_stream_id`:

- `StreamSupervisor` slot key va lifecycle methods.
- Gia tri `StreamSupervisor.add()` tra ve.
- `StreamSession` va `StreamSessionSnapshot.stream_id`.
- Operator-facing runtime logs.

Internal/runtime boundary dung `storage_id`:

- Playlist timeline observation generation.
- Active variant, runtime health va metrics Redis keys.
- Segment processing identity, state va lock.
- Black-screen/audio-loss canonical Redis state trong transition hien tai.

`SegmentProcessingIdentity.stream_id` da duoc rename thanh `storage_id` de tranh
consumer moi vo tinh dua external ID vao Redis processing key.

## Compatibility

Voi input cu:

```text
--stream-id channel-01
```

Redis van dung:

```text
sha256("channel-01")[:24]
```

Nen processing state/event state hien co khong bi tao namespace moi. Supervisor
va public snapshot bay gio tra `channel-01` thay vi hash.

## Limitation chuyen tiep

Alert publisher va event store hien van nhan mot `stream_id` chung cho ca Redis
ownership va public payload. Trong Phase 1 factory tiep tuc truyen `storage_id`
de bao ve Redis correctness. Vi vay Redis alert payload van co hashed stream ID.

Phase 2 se tach ro:

```text
storage_id          Redis keys, event identity, metrics
external_stream_id  AlertEnvelope.stream_id, WebSocket/API payload
```

Backend/UI owner nen tiep tuc dung fake `AlertSource` theo public contract va
chua bind UI vao raw Redis alert field cho den khi Phase 2 hoan tat.

## Runtime alert contract correction

`alert.schema.json` da bo sung alert da ton tai trong engine:

```text
event_type: RUNTIME_HEALTH
state: DEGRADED | RECOVERED
```

No nam cung envelope version `1.0` voi content alerts.

## Test coverage

Phase 1 co test cho:

- Explicit external ID duoc giu nguyen.
- Storage hash deterministic va compatible version cu.
- URL fallback identity.
- Empty/oversized external ID validation.
- Supervisor/session expose external ID.
- Scheduler tao processing identity bang storage ID.
- Redis processing/runtime keys khong chua external ID.
- Contract event/state match alerts hien tai.

Ket qua review:

```text
Targeted: 57 passed
Full:     236 passed, 16 skipped
```

15 Redis integration tests skip vi Redis local timeout. Mot long-live validation
skip vi can opt-in. Khong co test failure.

## Viec backend/UI co the lam sau Phase 1

- Dung external `stream_id` trong REST route, form va local state.
- Implement fake lifecycle theo `monitoring-command.schema.json`.
- Implement fake runtime snapshot theo `runtime-status.schema.json`.
- Hien thi `RUNTIME_HEALTH` ben canh content alerts neu can.
- Khong luu/display `storage_id` va khong truy cap raw Redis keys.

## Phase tiep theo

Phase 2 normalize alert output va truyen dong thoi external/storage identity vao
check persistence boundary. Sau Phase 2 backend co the thay fake alert source
bang Redis alert consumer ma khong lo hashed stream ID ra UI.
