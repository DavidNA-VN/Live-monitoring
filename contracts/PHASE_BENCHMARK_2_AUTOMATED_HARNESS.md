# Phase Benchmark 2 - Automated Capacity Harness

## Muc tieu

Phase nay bien cac metric cua Phase 1 thanh benchmark co the lap lai. Harness chay
tren production path that: local sliding HLS -> `MonitoringWorkerApplication` ->
variant selection -> admission/scheduler -> FFmpeg profiles -> Redis runtime metrics.

Baseline MVP mac dinh chi monitor `highest_quality`, mot stream va bat ca ba check.
Khong dung external live URL trong phase nay de ket qua khong phu thuoc CDN/token.

## Cach chay

Redis test database phai san sang. Fixture se duoc sinh tu dong neu chua ton tai.

```powershell
$env:REDIS_TEST_URL="redis://localhost:6379/15"
python .\scripts\run_capacity_benchmark.py `
  --checks all `
  --video-workers 2,4,6 `
  --audio-workers 1,2 `
  --global-process-limits 3,5,8 `
  --publisher-speeds 1,1.5,2 `
  --warmup-seconds 15 `
  --steady-seconds 60 `
  --cooldown-seconds 10
```

Chay nhanh de smoke test harness:

```powershell
python .\scripts\run_capacity_benchmark.py `
  --warmup-seconds 3 --steady-seconds 10 --cooldown-seconds 3
```

`psutil` la optional. Cai no neu can CPU, RAM va so FFmpeg process trong report:

```powershell
python -m pip install -r requirements-benchmark.txt
```

Khong co `psutil`, metric pipeline van duoc do va resource fields se la `null`.

## Ba khoang do

- `warm-up`: cho playlist va scheduler on dinh; khong dung lam SLO.
- `steady-state`: publisher tiep tuc o toc do live va harness lay mau dinh ky.
- `cooldown`: dung publisher, cho queue drain de quan sat kha nang hoi phuc.

## Output

Moi lan chay tao trong `benchmark_results/`:

- JSON day du: configuration, counter deltas, final Redis metrics, CPU/RAM.
- CSV gon: mot dong cho moi to hop worker/process limit.

Harness tinh (queue SLO chi dung mau steady-state, khong tron cooldown vao slope):

- queue lag start/end/max va linear slope;
- queue lag rieng sau cooldown de biet backlog co drain duoc hay khong;
- live-edge lag max;
- completed/failed/timed-out work;
- completed throughput theo `work/second`;
- dropped media segments va coverage-gap segments;
- peak active media process;
- CPU/RAM/FFmpeg process neu co `psutil`.

## SLO baseline

Mac dinh mot run FAIL neu:

- queue lag vuot 12 giay;
- queue slope vuot 0.05 giay lag tren moi giay thuc;
- co segment bi drop;
- co profile timeout hoac failure.

Day la nguong benchmark ban dau, khong phai production SLO cuoi cung. Phase 3 se
dung stream that de calibrate lai sau khi da chon duoc cau hinh local tot.
Phase 3 dung cung runner voi `--url`; URL/token khong nam trong report.

## Boundary

Phase nay khong:

- sua detector, policy hay event reducer;
- thay doi admission behavior;
- hardcode token/link production;
- prefetch media hoac tao mot decode path rieng;
- thay doi default runtime theo ket qua cua mot lan benchmark.

## Definition of Done

- Harness chay production path voi sliding fixture deterministic.
- Ho tro matrix video workers, audio workers va global process limit.
- Ho tro publisher speed matrix de tao deterministic overload.
- Moi run co warm-up, steady-state va cooldown.
- JSON/CSV report co queue slope, throughput, drop, timeout va resource usage.
- Co machine-readable PASS/FAIL va process exit code.
- Unit tests bao ve parser, counter delta, slope va SLO summary.

Throughput chi dem metric theo profile (`video_realtime`, `audio_realtime`, ...).
Metric `perf_resource_*` la mot view khac cua cung work va khong duoc cong lan hai.
