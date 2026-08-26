# Phase 2 - Public Alert Identity Boundary

## Trang thai

Phase 2 da tach public alert identity khoi Redis/storage identity cho
black-screen, repeated black-screen, audio-loss va runtime-health. Detection
rules va Redis canonical keyspace khong thay doi.

## Public alert contract

`AlertEnvelope.stream_id` luon la `external_stream_id`. Envelope co them
top-level `variant_stable_id`; field nay khong con bi duplicate trong audio-loss
attributes.

Public alert khong chua raw `storage_id`. Cac opaque `event_id` va `alert_id`
duoc derive deterministic tu storage identity de giu idempotency ma khong expose
gia tri storage.

## Internal storage boundary

`storage_id` tiep tuc owner cac tai nguyen sau:

- Black/audio open event, canonical event, lock va commit marker.
- Repeated-black rolling history va incident state.
- Segment/timeline processing state.
- Runtime health va metrics Redis keys.
- Deterministic content event/alert identity input.

Khong co Redis processing/runtime key moi duoc tao tu external stream ID.

## Runtime health

Runtime health Redis keys dung storage ID, nhung outbox payload dung external
ID. Runtime event ID da doi tu format lo raw storage hash sang UUID5 opaque:

```text
event_type = RUNTIME_HEALTH
event_id   = deterministic opaque UUID
stream_id  = external stream ID
```

Runtime alert ID cung deterministic theo storage identity, output state,
reason va payload. Retry cung mot transition khong tao alert ID moi.

## Backward compatibility

- Content event ID va alert ID van dung storage ID lam deterministic input.
- Redis canonical key schemas va namespace khong thay doi.
- Open event cu co hashed `stream_id` duoc reducer normalize sang external ID
  khi tiep tuc xu ly; original `event_id` duoc giu nguyen.
- Runtime health public event ID thay format mot lan de loai raw storage leak.
  Backend chua production-consume contract nay, nen khong can dual-read.
- Old records da nam trong Redis outbox co the van mang hashed stream ID. MVP
  consumer nen bat dau tai deployment cursor moi hoac chi ingest messages phat
  sau Phase 2; khong suy dien external ID tu old payload.

## Mapping cho backend/UI

Backend co the map Redis outbox thanh `alert.schema.json`:

```text
stream_id          public channel ID
variant_id         display label
variant_stable_id  stable rendition identity
event_id           group OPEN/UPDATE/RESOLVED
alert_id           deduplicate tung emission/retry
```

UI khong doc Redis truc tiep va khong parse event/alert ID. Hai ID nay la opaque
strings.

## Alert semantics

```text
BLACK_SCREEN           OPEN, RESOLVED
REPEATED_BLACK_SCREEN  OPEN, UPDATE, RESOLVED
AUDIO_LOSS             OPEN, RESOLVED
RUNTIME_HEALTH          DEGRADED, RECOVERED
```

Audio loss cause nam tai `attributes.primary_cause`:

```text
continuous_silence
audio_stream_missing
```

## Review findings da xu ly

Review phat hien runtime event ID con embed raw `storage_id`. Format nay da
duoc thay bang opaque deterministic UUID va co retry stability test.

## Test coverage

- AlertEnvelope Redis round-trip voi external stream va stable variant ID.
- Black/audio publisher public payload va storage metrics boundary.
- Content event/alert deterministic identity compatibility.
- Legacy open event normalization cho black va audio.
- Runtime Lua argument order, storage keys va external payload.
- Runtime event/alert retry identity stability.
- Full black/audio detection regression.

Ket qua review:

```text
Targeted before fix: 81 passed
Targeted after fix:  86 passed
Full regression:     243 passed, 16 skipped
```

15 Redis integration tests skip vi Redis local timeout. Mot long-live test skip
vi can opt-in. Lua contract co unit coverage; khong co test failure.

## Viec backend/UI co the lam sau Phase 2

- Thay fake alert DTO bang mapper/consumer doc Redis outbox moi.
- Route va filter alert bang external `stream_id`.
- Group lifecycle bang `event_id` va deduplicate bang `alert_id`.
- Filter rendition bang `variant_stable_id`, hien thi `variant_id`.
- Khong depend vao Redis key schema hoac format noi bo cua opaque IDs.

## Phase tiep theo

Phase 3 tao `MonitoringControl` port quanh `StreamSupervisor` cho lifecycle
START/PAUSE/RESUME/STOP/UPDATE_CONFIG. Backend se tiep tuc dung fake adapter;
Redis command adapter chi duoc noi vao o phase sau.
