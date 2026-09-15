# Phase Benchmark 1 - Performance Instrumentation

## 1. Goal

Do profile-work dang cho va ton thoi gian o dau ma khong thay detector, business
rule, admission decision hoac media execution path.

## 2. Instrumented lifecycle

```text
admission queue
  -> accepted executor submission
  -> executor queue wait
  -> profile work starts
  -> media process gate wait
  -> profile execution
  -> detector result processing
  -> event/Redis commit
  -> work completed or failed
```

Metric duoc aggregate theo hai dimension huu han:

- profile: `video_realtime`, `audio_realtime`, ...;
- resource: `resource_video_decode`, `resource_audio_decode`, ...

Khong co label/key theo segment sequence, URL, event ID hoac timeline.

## 3. Metrics

Voi moi dimension, Redis runtime metrics co cac field:

```text
perf_<dimension>_work_submitted_total
perf_<dimension>_work_started_total
perf_<dimension>_work_completed_total
perf_<dimension>_work_failed_total
perf_<dimension>_work_timed_out_total

perf_<dimension>_executor_wait_seconds_total|max|p50|p95|p99
perf_<dimension>_process_gate_wait_seconds_total|max|p50|p95|p99
perf_<dimension>_profile_execution_seconds_total|max|p50|p95|p99
perf_<profile>_result_processing_seconds_total|max|p50|p95|p99
perf_<profile>_result_commit_seconds_total|max|p50|p95|p99
```

Them service capacity gauge:

```text
active_media_processes
max_media_processes
peak_active_media_processes
```

`*_total` la counter tich luy trong Redis. `*_max` va percentile la gauge cua
chu ky runtime moi nhat.

## 4. Percentile semantics

- Samples nam trong RAM cua `RuntimeMetricCollector`.
- Toi da 2048 samples cho moi metric trong mot drain cycle.
- Dung nearest-rank p50/p95/p99.
- Sau `drain()`, samples duoc xoa.
- Redis chi nhan gia tri percentile aggregate, khong nhan raw samples.

## 5. Timing boundaries

### Executor wait

Tu luc `BoundedExecutor` chap nhan batch den khi thread bat dau chay batch.
Mot batch wait sample duoc ap cho moi profile-work trong batch khi tinh total.

### Process gate wait

Thoi gian cho per-stream/service media process budget. Gate duoc release bang
`finally`, ke ca instrumentation hoac profile nem exception.

### Profile execution

Bao gom command build, FFmpeg process, FFmpeg tu mo media URL, demux, decode,
filter va parse stderr output.

Khong the tach `segment_fetch_seconds` chinh xac trong kien truc hien tai vi
FFmpeg tu fetch resource. Prefetch segment se thay execution path, tang I/O va
khong thuoc Phase 1. Playlist fetch latency van duoc do rieng boi runtime.

### Result processing va commit

- processing: detector/policy tao processor outcome;
- commit: event store/Redis commit cua outcome;
- segment processing state transition nam ngoai commit timer hien tai.

## 6. How to interpret

- executor p95 cao, process-gate p95 thap: executor pool/dispatch dang cho;
- process-gate p95 cao: media process budget dang gioi han;
- profile execution p95 cao: fetch/FFmpeg/decode/filter/parse la nhom can tach
  tiep trong benchmark;
- result processing/commit cao: Python business logic hoac Redis la bottleneck;
- submitted tang nhanh hon completed: throughput thap hon input rate;
- peak active thap hon gate trong khi queue tang: khong nen ket luan CPU full,
  can kiem tra executor va profile execution.

## 7. Non-goals

Phase nay khong:

- sua threshold hay event lifecycle;
- prefetch/download segment;
- them OS-specific CPU/RAM collector vao domain worker;
- tuning worker/process default;
- thay Redis key theo tung segment;
- sua Dashboard/alert presentation;
- ket luan capacity tu fixture 160x90.

## 8. Definition of Done

- moi accepted profile-work co submitted/started/final state metrics;
- executor, gate, profile, processing va commit co timing aggregate;
- p50/p95/p99 khong persist raw samples;
- active/max/peak media process co the quan sat;
- exception khong lam leak process gate;
- metrics duoc day qua runtime Redis hash hien tai;
- targeted concurrency/metrics tests va full regression pass;
- Phase 2 co the lay Redis counter delta de tinh input rate/throughput.
