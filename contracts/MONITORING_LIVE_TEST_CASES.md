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
| `black_screen` | black 6-8, 14-16, 22-24, 30-38s | black on, freeze off | repeated short-black + direct long-black alert |
| `audio_silence` | silence 4-39s, audio track present | audio-loss on | `continuous_silence`, then resolved |
| `audio_missing` | 40s without audio elementary stream | audio-loss on | `audio_stream_missing` after 30s |
| `video_freeze` | 2.9s, three 3.2s and one 5.2s freezes | freeze on, black off | boundary, direct alert and repeated warning |
| `combined` | black + freeze + 35s silence | all enabled | independent alerts through shared video profile |

Black frames are static. Khi freeze cung enable, mot black range dai co the thoa ca
hai detector va phat hai event doc lap. Day la expected baseline behavior, khong
phai duplicate alert.

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

## Start live environment

Terminal 1, HTTP server:

```powershell
python scripts/serve_hls.py
```

Terminal 2, publisher vi du:

```powershell
python scripts/publish_monitoring_test_stream.py video_freeze --reset
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
http://127.0.0.1:8000/live_cases/combined/master.m3u8
```
