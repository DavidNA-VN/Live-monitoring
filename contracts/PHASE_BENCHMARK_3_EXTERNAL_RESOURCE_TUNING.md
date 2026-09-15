# Phase Benchmark 3 - External Live Resource Tuning

## Muc tieu

Dung master HLS that de xac dinh bottleneck va preset phu hop cho MVP mot stream,
mot variant chat luong cao nhat, ba check black-screen, video-freeze va audio-loss.

Phase nay khong thay business rule, detector, reducer hoac admission policy.

## Bao mat input

- External URL chi duoc truyen qua `--url` va giu trong memory cua run.
- JSON/CSV chi ghi `source_kind=external_live`, khong ghi URL hoac token.
- Khong dua external URL vao test, contract, source hay commit.
- Moi run dung Redis namespace ngau nhien va xoa namespace khi ket thuc.

## Workload hien tai

Manifest duoc inspect truoc run, nhung khong persist URL. Tai thoi diem Phase 3:

- 5 video variants;
- H.264/AAC;
- highest-quality policy chon 1920x1080, 50 fps, average bandwidth xap xi
  10.7 Mbps;
- chi mot stream va mot selected video variant duoc certify.

## Thu tu benchmark

Chay tung preset rieng de ket qua de doc va tranh nhieu run su dung URL het han:

```text
conservative: video=2, audio=1, global=3, per-stream=3
baseline:     video=4, audio=1, global=5, per-stream=5
expanded:     video=6, audio=2, global=8, per-stream=8
```

Moi run toi thieu 15 giay warm-up va 30-60 giay steady-state. External live
khong co cooldown vi khong the dung CDN publisher; cooldown trong report bang 0.

## Lenh mau

```powershell
$env:LIVE_BENCHMARK_URL="<master-m3u8>"
python .\scripts\run_capacity_benchmark.py `
  --url $env:LIVE_BENCHMARK_URL `
  --checks all `
  --video-workers 2 `
  --audio-workers 1 `
  --global-process-limits 3 `
  --per-stream-process-limit 3 `
  --warmup-seconds 15 `
  --steady-seconds 60
```

Nen chay tung preset bang mot gia tri video/audio/global moi lenh, khong chay
full Cartesian matrix trong buoc tuning dau tien.

## Cach doc bottleneck

- Executor wait cao, process-gate wait thap: resource pool dang thieu worker.
- Process-gate wait cao, CPU con headroom: gate co the tang tung buoc.
- CPU cao va profile execution p95 tang: concurrency da qua diem tot.
- Gate wait thap, CPU thap, queue van tang: dieu tra network/fetch/dispatch.
- Queue slope duong hoac dropped segment: preset khong du capacity realtime.
- Tang worker ma throughput khong tang: bottleneck khong nam o worker count.

## Definition of Done

- External run khong luu URL/token vao artifact.
- Co report cho conservative, baseline va expanded preset.
- So sanh queue slope, p95 execution/wait, CPU/RAM, peak FFmpeg va drop.
- P95 trong report la gia tri lon nhat quan sat qua cac cycle steady-state,
  khong chi la cycle Redis cuoi cung.
- Chon preset dua tren queue slope <= 0, zero timeout/failure/drop va CPU co
  headroom; khong chon chi dua tren peak throughput.
- Ghi ro ket qua chi certify mot stream/variant/workload da do.

## Preliminary result - 2026-09-07

Workload: mot selected 1080p50 H.264 variant, AAC muxed audio, segment 4 giay,
video profile gom black+freeze va audio-loss profile chay song song.

| Preset | Lag start -> end | Queue slope | Work/s | Peak FFmpeg | CPU mean/max | Video p95 | Audio p95 | Drop |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2/1/gate3 | 7.87s -> 2.05s | -0.164 | 0.567 | 2 | 11.7% / 21.1% | 2.88s | 1.90s | 0 |
| 4/1/gate5 | 6.01s -> 0.00s | -0.212 | 0.567 | 2 | 13.2% / 42.9% | 2.56s | 2.16s | 0 |
| 6/2/gate8 | 7.97s -> 0.00s | -0.306 | 0.633 | 2 | 15.4% / 35.7% | 1.83s | 2.18s | 0 |

Ca ba run 30 giay steady-state deu zero timeout/failure/drop. Peak chi dat 2
FFmpeg va process-gate wait gan 0, nen gate khong phai bottleneck trong workload
mot variant nay. Chua doi runtime default: can soak run dai hon truoc khi certify
preset.

### Soak va startup result

| Preset | Warm-up / steady | Lag start -> end | Lag max | Slope | Work/s | CPU mean/max | Drop | Result |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2/1/gate3 | 15s / 180s | 12.18s -> 0s | 14.17s | -0.0427 | 0.528 | 17.8% / 35.3% | 0 | FAIL startup SLO |
| 4/1/gate5 | 15s / 180s | 11.97s -> 0s | 12.03s | -0.0291 | 0.522 | 13.8% / 50.2% | 0 | FAIL startup SLO |
| 2/1/gate3 | 30s / 60s | 2.24s -> 2.07s | 2.68s | +0.0091 | 0.467 | 18.3% / 48.8% | 0 | PASS steady SLO |

Hai soak 180 giay cho thay throughput trung binh tren input rate xap xi 0.5
profile-work/s va queue ve 0, nhung warm-up 15 giay chua du de dọn bounded startup
history. Tang pool/gate khong thay doi peak 2 FFmpeg va khong giai quyet startup
lag mot cach dang ke.

### Ket luan Phase 3

- Bottleneck cua workload nay khong nam o executor pool hay process gate.
- `2 video workers / 1 audio worker / gate 3` la preset tam du cho MVP mot stream,
  highest-quality variant va ba check hien tai.
- Runtime default chua duoc doi vi can benchmark dai hon va them network samples.
- Startup catch-up latency phai duoc theo doi nhu SLO rieng; khong che no bang
  cach tang fixed warm-up trong production.
- Certification production van can soak 6h/24h; ket qua Phase 3 chi xac nhan
  architecture va huong tuning tren may demo.
