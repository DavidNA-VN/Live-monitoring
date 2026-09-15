# Monitoring Live Test Cases

## Components

- `scripts/generate_monitoring_test_cases.py` tao deterministic two-variant VOD
  HLS fixtures va `expected.json`.
- `scripts/publish_monitoring_test_stream.py` publish mot fixture thanh sliding
  live HLS bang engine trong `publish_live_hls.py`.
- `scripts/serve_hls.py` expose `hls_output` tai port 8000.

## Case matrix

| Case | Timeline | Checks khi test rieng | Expected |
|---|---|---|---|
| `healthy` | 24s motion + audible | all enabled | no content alert |
| `black_screen` | black 6-8.2, 14-16.2, 22-24.2, 30-92s | black on, freeze off | repeated OPEN/RESOLVED + continuous OPEN/RESOLVED on highest variant |
| `audio_silence` | silence 4-39s, audio track present | audio-loss on | `continuous_silence` OPEN, then RESOLVED |
| `audio_missing` | 40s without audio elementary stream | audio-loss on | `audio_stream_missing` OPEN after 30s; remains open |
| `video_freeze` | three 4s freezes, then one 62s freeze | freeze on, black off | repeated OPEN/RESOLVED + continuous OPEN/RESOLVED per variant |
| `macroblocking` | 40% pixelated region from 4-18s | macroblocking on, other checks off | one OPEN and one RESOLVED on highest variant |
| `combined` | repeated black, repeated freeze, 35s silence, 14s macroblocking | all enabled | four independent OPEN/RESOLVED lifecycles |

Black frames are static, nhung shared video profile loai black overlap khoi
effective freeze. Vi vay cung mot khoang black khong duoc phat dong thoi thanh
VIDEO_FREEZE.

Fixture `black_screen` dung segment 2s. Ba event ngan dai 2.2s de co sampling
margin va chac chan dat dieu kien `>= one segment`; chung duoc dua vao repeated
counter sau khi dong. Event 30-92s dai 62s, vi vay dat
continuous threshold 60s khi dang dien ra. Segment healthy day du ke tiep xac
nhan `RESOLVED`; `expected.json` la expected-output machine-readable chinh thuc.

## Generate fixtures

Tu project root:

```powershell
python scripts/generate_monitoring_test_cases.py --reset
```

Chi generate mot hoac vai case:

```powershell
python scripts/generate_monitoring_test_cases.py --reset --case healthy --case video_freeze
```

`--reset` chi xoa directory co ownership marker cua generator.

## Automated production verification

Khi Redis va `scripts/serve_hls.py` dang chay:

```powershell
python scripts/verify_monitoring_test_cases.py --speed 1
```

Runner khoi dong `live_main.py` cho tung case, dung Redis namespace rieng, chi
monitor highest-quality variant, va so sanh content alert voi `expected.json`.
Missing, unexpected, duplicate alert hoac dropped media segment deu lam command
tra exit code `2`.

## Start live environment

Terminal 1, HTTP server:

```powershell
python scripts/serve_hls.py
```

Terminal 2, publisher vi du:

```powershell
python scripts/publish_monitoring_test_stream.py video_freeze --reset
```

Macroblocking:

```powershell
python scripts/publish_monitoring_test_stream.py macroblocking --reset
```

URL:

```text
http://127.0.0.1:8000/live_cases/video_freeze/master.m3u8
```

Dung `Ctrl+C` de tat publisher. Muon doi case tren cung Dashboard stream ID:

1. STOP stream cu va doi den STOPPED/404.
2. Tat publisher cu bang `Ctrl+C`.
3. Chay publisher case moi voi `--reset`.
4. Paste URL moi va START lai.

Khong chi F5 Dashboard: F5 khong xoa worker session hoac desired state trong
Redis.

## Useful publisher options

```powershell
python scripts/publish_monitoring_test_stream.py combined `
  --reset `
  --window-size 6 `
  --retention-segments 12 `
  --start-sequence 1000 `
  --speed 1
```

- `--speed 1`: realtime.
- `--speed 10`: accelerated manual smoke test.
- `--max-publishes N`: dung sau N segments.
- Sequence/URI/PDT tang qua moi loop; loop boundary co discontinuity.

## URLs

```text
http://127.0.0.1:8000/live_cases/healthy/master.m3u8
http://127.0.0.1:8000/live_cases/black_screen/master.m3u8
http://127.0.0.1:8000/live_cases/audio_silence/master.m3u8
http://127.0.0.1:8000/live_cases/audio_missing/master.m3u8
http://127.0.0.1:8000/live_cases/video_freeze/master.m3u8
http://127.0.0.1:8000/live_cases/macroblocking/master.m3u8
http://127.0.0.1:8000/live_cases/combined/master.m3u8
```
