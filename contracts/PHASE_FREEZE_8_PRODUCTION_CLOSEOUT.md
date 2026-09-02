# Phase Freeze 8 - Production Closeout

## Scope

Phase nay dong implementation baseline cua `VIDEO_FREEZE`. Production pilot van
phai benchmark va soak tren target host voi live sources dai dien; do khong phai
ly do de chen source-specific heuristic vao detector.

## Architecture review

Pipeline sau khi dong freeze:

```text
HLS segment
  -> admission + timeline/media identity
  -> shared VideoRealtimeProfile
       -> one FFmpeg process
       -> blackdetect + freezedetect when requested
  -> independent processors
       -> black-screen domain
       -> video-freeze domain
  -> disease-owned Redis state
  -> durable shared alert outbox
  -> REST history + WebSocket realtime
```

Invariants da verify:

- Black va freeze dung chung mot video process khi cung enable.
- Freeze khong import black-screen/audio-loss; presentation khong import private
  freeze domain hoac Redis keys.
- Event/commit identity giu timeline generation va manifest-visible media
  revision.
- UNKNOWN/timeout khong duoc xem la motion returned.
- Warning, alert escalation va lifecycle state la hai concept doc lap.
- Outbox co MAXLEN; event, commit, warning history va repeated incident co TTL.
- Global media process gate van la ceiling chung; enable freeze khong tao them
  executor hoac tang worker budget.

## Operational metrics

Runtime metrics hien co:

- `video_analysis_total`
- `video_analysis_failure_total`
- `video_analysis_timeout_total`
- `video_freeze_interval_total`
- `video_freeze_seconds_total`
- `video_freeze_warning_total`
- `video_freeze_alert_total`
- `video_freeze_resolved_total`
- `video_freeze_interrupted_total`

Profile metrics phan biet detector observations voi public business alerts.
Khong suy ra alert count tu interval count.

## Local benchmark baseline

Command:

```powershell
$env:PYTHONPATH="src;."
python scripts/benchmark_detection_profiles.py --iterations 3
```

Mot calibration run tren fixture 160x90, 10 giay, 5 segments cho ket qua:

| Mode | Process/segment | Realtime factor |
|---|---:|---:|
| black | 1 | 0.0257 |
| freeze | 1 | 0.0249 |
| black + freeze | 1 | 0.0244 |
| black + freeze + audio | 2 | 0.0421 |

Day chi la regression baseline tren may local, khong phai production sizing.
Capacity phai do lai theo codec, resolution, variant count, segment duration,
source transport va CPU cua target host.

## Calibration assumptions

`freezedetect` do frame similarity. No khong the tu media signal phan biet:

- actual encoder/player frozen frames;
- camera tinh, slide/slate tinh hoac canh rat it chuyen dong;
- black frame lien tuc, co the dong thoi thoa black-screen va freeze.

Baseline vi vay detect actual repeated/still frames theo threshold va khong suy
luan intentional/unintentional. Black va freeze co the phat hai event doc lap neu
ca hai rule cung thoa. Truoc production pilot can calibration bang dataset gom
actual freeze, static camera, slides/slates, low-motion, black va motion normal.

## Bounded-state verification

Accelerated Redis stress test tao 40 freeze lifecycles va verify:

- rolling history chi giu records trong 120 giay;
- alert stream khong vuot configured MAXLEN;
- canonical event details co event TTL;
- segment commit markers co commit TTL;
- repeated state co bounded window va expiry.

Long-duration live soak van can chay tren deployment target. Acceptance toi thieu:

- process active count khong vuot global ceiling;
- queue depth va lag hoi phuc sau burst;
- khong tang memory/key count vo han sau TTL horizon;
- khong duplicate alert sau worker/API restart;
- realtime factor duoi 1 tai concurrency du kien.

## Pub/Sub extension boundary

Redis Stream tiep tuc la source of truth. Neu can scale WebSocket fan-out, them
relay/AlertHub sau durable stream. Freeze publisher khong dual-write Pub/Sub va
khong import Pub/Sub client; nhu vay delivery scaling khong yeu cau sua detector,
processor, reducer hoac repository.

## Closeout

Freeze baseline du dieu kien dong ve application architecture va detector flow.
Cong viec con lai thuoc production pilot: dataset calibration, long soak va
capacity sizing tren target environment.
