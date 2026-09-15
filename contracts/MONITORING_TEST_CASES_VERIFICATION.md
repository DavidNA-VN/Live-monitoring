# Monitoring Test Cases Verification

Verification date: 2026-09-14.

The deterministic HLS catalog was regenerated after the current black-screen,
video-freeze, audio-loss, and macroblocking business rules were finalized.
Every production run selected one highest-quality variant and used an isolated
Redis namespace.

## Results

| Case | Expected and observed content lifecycle | Dropped segments | Result |
|---|---|---:|---|
| `healthy` | no content alert | 0 | PASS |
| `black_screen` | repeated OPEN/RESOLVED; continuous OPEN/RESOLVED | 0 | PASS |
| `video_freeze` | repeated OPEN/RESOLVED; continuous OPEN/RESOLVED | 0 | PASS |
| `audio_silence` | AUDIO_LOSS OPEN/RESOLVED | 0 | PASS |
| `audio_missing` | AUDIO_LOSS OPEN, no false RESOLVED | 0 | PASS |
| `macroblocking` | MACROBLOCKING OPEN/RESOLVED | 0 | PASS |
| `combined` | repeated black, repeated freeze, audio loss, and macroblocking OPEN/RESOLVED | 0 | PASS |

No case produced an unexpected content alert. Runtime-health messages are
reported separately and are not treated as detector output.

## Important fixture corrections

- Short black events are 2.2 seconds, giving frame-sampling margin above the
  one-segment (2 second) repeated-event lower bound.
- Black and silence filter ranges use half-open `[start, end)` semantics.
- Macroblocking recovery uses deterministic healthy evidence, preventing a
  natural `testsrc2` frame from extending the event by one segment.
- `combined` now includes all four checks without overlapping fault windows.
- `expected.json` now contains recommended check switches and explicit alert
  lifecycles for audio-loss cases.

Machine-readable reports from the verification run are under `.benchmark/`.
