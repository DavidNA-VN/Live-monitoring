# Phase Macroblocking 1 - Ground Truth and Benchmark Harness

Status: Complete.

## Artifacts

- `scripts/generate_macroblocking_fixtures.py` generates deterministic,
  single-highest-variant HLS fixtures and an ASCII JSON manifest.
- `scripts/benchmark_macroblocking.py` validates manifests and scores detector
  observation files independently of detector implementation.
- Unit tests cover manifest semantics, safe reset ownership, canonical
  configuration fingerprinting, area-band scoring and natural-grid false
  positives.
- The integration test generates all HLS assets with FFmpeg and reads every
  master playlist through FFprobe.

Generated media lives below `hls_output/macroblocking_fixtures` and is ignored
by Git. Source definitions, manifest generation and tests are tracked.

## Fixture matrix

| Fixture | Label | Approximate affected area |
| --- | --- | --- |
| `clean_motion` | Negative | N/A |
| `natural_periodic_grid` | Hard negative | N/A |
| `local_small` | Positive, 2-14s | 14-18% |
| `local_large` | Positive, 2-14s | 36-44% |
| `full_frame` | Positive, 2-14s | 90-100% |

The positive images use a deterministic synthetic block grid. They test spatial
localization and temporal boundaries, but they are not claimed to represent all
production codec artifacts. Real captured examples remain required before the
Phase 8 production threshold is accepted.

## Prediction input boundary

The scorer consumes observations with fixture name, timestamp, candidate flag
and predicted affected-area ratio. It calculates TP/FP/TN/FN, precision, recall,
area-band accuracy and distance outside the expected area band. Classification
and area correctness are therefore measured separately.

The optional performance envelope records frame count, wall time and peak
memory, from which the scorer derives frames/second and mean frame latency.
Every report includes a SHA-256 fingerprint of canonical analyzer configuration.

## Commands

```powershell
python scripts/generate_macroblocking_fixtures.py --reset
python scripts/benchmark_macroblocking.py `
  --manifest hls_output/macroblocking_fixtures/manifest.json
```

Phase 2 will add detector prediction generation and optional heatmap PNGs. Raw
heatmaps are debug artifacts and never become manifest truth or Redis state.

## Definition of Done

- Positive labels contain temporal ranges and approximate area bands.
- A periodic-grid hard negative exists.
- Reset cannot delete a directory the generator does not own.
- Generated HLS is readable and has the expected 640x360 highest variant.
- Scoring detects correct classification with incorrect spatial area.
- Results identify analyzer configuration and report performance consistently.
