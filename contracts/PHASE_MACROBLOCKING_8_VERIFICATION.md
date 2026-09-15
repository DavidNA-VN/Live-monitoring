# Phase 8 - Macroblocking Verification and Production Gate

Status: deterministic verification tooling and canonical HLS fixture complete.

## Scope completed

- A repeatable fixture matrix covers 480x270 and 640x360, 1/3/5/10 FPS,
  and all configured fusion strategies.
- The runner reports precision, recall, area-band accuracy, throughput, and
  realtime ratio for every configuration.
- An external-source path reports candidate coverage, the longest continuous
  candidate run, analyzer latency, and whether the 15% for 10 seconds rule
  would open an event.
- `--expect-source-event` turns the known-positive smoke test into a failing
  acceptance gate instead of silently producing a misleading green report.
- Macroblocking remains opt-in and continues to share the existing video
  decode pass. No Redis, public alert, or lifecycle contract was weakened.

## Reproducible commands

Quick deterministic benchmark:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_macroblocking_phase8.py `
  --quick `
  --output .\.benchmark\macroblocking\phase8-quick.json
```

Full configuration matrix:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_macroblocking_phase8.py `
  --output .\.benchmark\macroblocking\phase8-matrix.json
```

The full matrix is deliberately not part of normal CI because 5/10 FPS cases
are capacity experiments and can take tens of minutes on a laptop.

## Measured result

The quick fixture configuration at 480x270, 1 FPS, and
`max_with_consistency` produced:

- precision: 0.9231;
- recall: 1.0000;
- area-band accuracy: 0.6389;
- throughput: 1.09 analyzed FPS, or 1.09x realtime.

The supplied 54.23-second screen-recorded HLS sample produced:

- 54 sampled frames;
- 3 candidate frames;
- 5.56% candidate coverage;
- longest continuous candidate run: 2 seconds;
- no 10-second event;
- mean analyzer time: about 876 ms/frame.

The sample is a recording of already damaged playback and was encoded again.
It decodes without FFmpeg corruption warnings and does not preserve a
dependable codec-block grid. It is classified as `screen-recorded-pixelation`,
not as the canonical macroblocking acceptance fixture. It remains useful for
a future perceptual-quality detector.

The canonical fixture is generated directly by
`generate_monitoring_test_cases.py`. It has a 40 percent pixelated region from
4s through 18s, crosses segment boundaries, exceeds the 10-second rule, and
contains healthy media before and after the incident.

At the production analyzer defaults (480x270, 1 FPS,
`max_with_consistency`), the generated high variant produced 24 observations,
17 positive observations, a longest continuous positive run of 16 seconds,
and therefore passed the 10-second event gate. Measured analyzer throughput
was 1.18 FPS on the development laptop.

Generate and publish it with:

```powershell
python scripts/generate_monitoring_test_cases.py --reset --case macroblocking
python scripts/publish_monitoring_test_stream.py macroblocking --reset
```

The live URL is:

```text
http://127.0.0.1:8000/live_cases/macroblocking/master.m3u8
```

## Decision

The screen recording must not be used to lower detector thresholds. Doing so
causes severe false positives on deterministic clean and natural-grid
fixtures. That is threshold overfitting, not a valid fix.

The next detector iteration needs a complementary no-reference pixelation or
blocking feature and a labeled set of real clean, natural-grid, and damaged
clips. The acceptance gate is:

- precision >= 0.90;
- recall >= 0.90;
- canonical directly-generated HLS opens and resolves the 10-second event;
- analyzer throughput >= configured sampling FPS;
- all lifecycle, Redis, shared-decode, API, and WebSocket tests remain green.

Until those conditions pass, the existing opt-in flag is the safe product
boundary. Event persistence and UI wiring are ready; image-level detection is
the remaining blocker.
