# Phase Realtime 0 - Baseline And Acceptance Freeze

## Goal

Phase 0 khoa workload va cach do cho single-stream realtime scaling. Phase nay
khong thay scheduler, detector, reducer, business threshold hoac public alert.

Certified target se duoc danh gia o cac phase sau:

```text
stream:            1
video variant:     highest quality only
audio rendition:   muxed hoac toi da 1 rendition duoc video tham chieu
current checks:     black-screen + video-freeze + audio-loss + macroblocking
segment duration:  fixture baseline 2 seconds
```

## Benchmark contract

`scripts/run_capacity_benchmark.py --checks all` bat du bon check. Moi run dung
Redis namespace ngau nhien va khong persist signed external URL.

Report lay mau tu luc worker START den het cooldown va tach counter thanh:

- `warmup_metric_deltas`;
- `metric_deltas` cho steady-state;
- `cooldown_metric_deltas`.

Drop va coverage gap co field rieng:

```text
warmup_dropped_media_segments
steady_dropped_media_segments
cooldown_dropped_media_segments
warmup_coverage_gap_segments
steady_coverage_gap_segments
cooldown_coverage_gap_segments
```

`dropped_media_segments` va `coverage_gap_segments` van la tong cua ba phase de
giu consumer report cu. Benchmark PASS/FAIL chi so sanh
`steady_dropped_media_segments` voi `--allow-dropped-segments`; warm-up va
cooldown van duoc cong khai, khong bi bo qua.

## Deterministic acceptance matrix

Canonical input la `scripts/generate_monitoring_test_cases.py` va tung
`expected.json`. Production verification chi chon highest-quality variant.

| Case | Enabled checks | Expected content lifecycle |
| --- | --- | --- |
| `healthy` | all | no content alert |
| `black_screen` | black | repeated OPEN/RESOLVED; continuous OPEN/RESOLVED |
| `video_freeze` | freeze | repeated OPEN/RESOLVED; continuous OPEN/RESOLVED |
| `audio_silence` | audio | AUDIO_LOSS OPEN/RESOLVED |
| `audio_missing` | audio | AUDIO_LOSS OPEN, no false RESOLVED |
| `macroblocking` | macroblocking | MACROBLOCKING OPEN/RESOLVED |
| `combined` | all four | independent OPEN/RESOLVED lifecycles |

`combined` cung publisher speed `1.5` va `2.0` la deterministic overload input;
khong can tao mot media fixture trung lap. Accuracy regression dung speed `1.0`.

## External pre-refactor baseline

Ngay 2026-09-15, external 2-second HLS workload cho ket qua:

| Preset | Video p95 | Audio p95 | Lag max | Dropped | Peak FFmpeg |
| --- | ---: | ---: | ---: | ---: | ---: |
| video 4 / audio 1 / gate 4, run 1 | 2.09s | 1.22s | 9.09s | 0 | 2 |
| video 2 / audio 1 / gate 3 | 6.08s | 3.52s | 26.57s | 16 | 2 |
| video 4 / audio 1 / gate 4, run 2 | 3.45s | 1.06s | 17.94s | 12 | 2 |

Artifacts nam trong `.benchmark/pre-refactor-real/`; external URL/token khong
nam trong report.

Ket qua nay khong chung nhan preset production. No chung minh throughput bien
dong, peak FFmpeg chi dat 2 va tang worker/gate khong tu dong loai bo drop.

## Commands

Targeted tests:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/scripts/test_run_capacity_benchmark.py `
  tests/scripts/test_verify_monitoring_test_cases.py `
  tests/scripts/test_generate_monitoring_test_cases.py -q
```

Deterministic baseline:

```powershell
.\.venv\Scripts\python.exe scripts/run_capacity_benchmark.py `
  --case combined `
  --checks all `
  --video-workers 4 `
  --audio-workers 1 `
  --global-process-limits 4 `
  --per-stream-process-limit 4 `
  --warmup-seconds 120 `
  --steady-seconds 600 `
  --cooldown-seconds 60
```

Deterministic overload them `--publisher-speeds 1.5` hoac `2` va dung output
directory rieng.

## Definition of Done

- `all` bat du bon check hien tai;
- report tach warm-up, steady-state va cooldown counter;
- deterministic 2-second fixture va expected lifecycle duoc khoa;
- external URL/token khong nam trong artifact;
- targeted tests va full regression pass;
- baseline khong thay detection/business behavior.

