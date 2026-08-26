# Phase 0 - MVP Integration Contracts

## Muc tieu

Phase 0 chot boundary giua monitoring engine va backend/UI. Detection logic
khong thay doi trong phase nay. Backend co the dung fake adapter theo cac schema
trong thu muc nay ma khong can import code detector.

## Contract da chot

| File | Vai tro |
| --- | --- |
| `stream-config.schema.json` | Cau hinh public cho mot live HLS stream |
| `monitoring-command.schema.json` | Lenh lifecycle gui toi monitoring worker |
| `runtime-status.schema.json` | Snapshot trang thai public tra cho API/UI |
| `alert.schema.json` | Alert realtime va persistent history |

Tat ca contract MVP dung `schema_version = "1.0"`. Field moi co the duoc bo sung
theo cach backward-compatible; doi ten, xoa field hoac doi semantics phai tang
major version.

## Mapping voi engine hien tai

- `AlertDTO` dua tren `src/models/alert.py::AlertEnvelope`.
- `StreamConfigDTO` map vao `src/models/stream_config.py::StreamConfig`.
- `RuntimeStatusDTO` se duoc map tu `StreamSupervisor.snapshots()` va runtime
  health; backend khong duoc serialize truc tiep internal dataclass.
- `MonitoringCommandDTO` se duoc command adapter xu ly qua
  `StreamSupervisor`; backend khong goi detector/profile truc tiep.

## Quy uoc identity

`stream_id` trong moi public contract la ID nguoi dung/backend cung cap, vi du
`channel-01`. Hash dung cho Redis key se la `storage_id` noi bo va khong duoc
dua ra UI. Engine hien tai chua tach hai identity nay; day la Phase 1.

`variant_id` la nhan hien thi. `variant_stable_id` la identity rendition on
dinh, can co trong alert khi backend can correlation/deduplication theo variant.

## Alert semantics hien tai

- `BLACK_SCREEN`: `OPEN`, `RESOLVED`.
- `REPEATED_BLACK_SCREEN`: `OPEN`, `UPDATE`, `RESOLVED`.
- `AUDIO_LOSS`: `OPEN`, `RESOLVED`.
- `RUNTIME_HEALTH`: `DEGRADED`, `RECOVERED`.
- Audio loss phan biet `continuous_silence` va `audio_stream_missing` qua
  `attributes.primary_cause`.
- Short black 0-3 giay khong publish tung warning; no tham gia rolling rule tao
  `REPEATED_BLACK_SCREEN`.
- Cung mot domain event giu nguyen `event_id`; moi emission co `alert_id`
  deterministic rieng de consumer deduplicate.

## Ownership khi lam song song

Monitoring owner:

- `src/`, worker adapter va cac schema trong `contracts/`.
- External/storage identity, command adapter, runtime mapper, Redis outbox.

Backend/UI owner:

- `backend/` va `frontend/`.
- REST, WebSocket, fake adapters va UI.

Backend/UI khong truy cap raw Redis key, khong import `checks`, `profiles` hoac
detector. UI chi nhan public DTO. Moi thay doi schema can duoc hai ben review
truoc khi merge.

## Viec backend/UI co the lam ngay

1. Tao fake `MonitoringControl` cho START/PAUSE/RESUME/STOP/UPDATE_CONFIG.
2. Tao fake `AlertSource` phat ba `event_type` trong `alert.schema.json`.
3. Hoan thien REST lifecycle va WebSocket reconnect theo public contract.
4. Deduplicate client bang `alert_id`; group lifecycle bang `event_id`.
5. Khi engine adapter san sang, thay fake implementation ma khong doi endpoint
   hoac UI model.

## Viec con lai phia engine

1. Phase 1: tach external `stream_id` va internal `storage_id`.
2. Phase 2: normalize alert public payload, gom `variant_stable_id`.
3. Phase 3: monitoring control port va command adapter.
4. Phase 4: runtime status mapper.
5. Phase 5: Redis command consumer va end-to-end integration.

## Acceptance Phase 0

- Bon JSON Schema parse duoc va cung dung version `1.0`.
- Event/check/state trong schema match hai case detection hien tai.
- Public contract khong lo Redis key hoac internal processing identity.
- Backend co the bat dau bang fake adapter ma khong can sua detection engine.
