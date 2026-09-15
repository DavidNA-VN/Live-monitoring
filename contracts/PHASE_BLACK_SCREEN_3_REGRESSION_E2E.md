# Black-screen Rule Revision - Phase 3

Status: Complete

## Scope

Phase 3 verifies the revised black-screen rule from generated media through:

```text
HLS fixture -> sliding live publisher -> worker -> Redis outbox
            -> REST history -> WebSocket dashboard contract
```

This phase does not change detector thresholds, scheduler admission, variant
selection, audio-loss, or video-freeze policy.

## Canonical fixture

The `black_screen` fixture is 96 seconds long with two-second source segments:

| Range | Duration | Expected classification |
| --- | ---: | --- |
| 6-8s | 2s | repeated candidate 1 |
| 14-16s | 2s | repeated candidate 2 |
| 22-24s | 2s | repeated candidate 3 |
| 30-92s | 62s | continuous black |

Expected public lifecycle per monitored variant:

```text
REPEATED_BLACK_SCREEN OPEN
REPEATED_BLACK_SCREEN RESOLVED (healthy_segment_confirmed)
BLACK_SCREEN OPEN
BLACK_SCREEN RESOLVED (healthy_segment_confirmed)
```

The generated `expected.json` contains this lifecycle in `expected_alerts` so
automation and manual verification use the same source of truth.

## Automated coverage

- Pure policy and reducer boundaries cover 59.999s, 60s, 100ms merge, 500ms
  split, same-segment and cross-segment continuity.
- Redis integration covers exactly-once OPEN/RESOLVED, retries, worker restart,
  lock contention, observation gaps, timeline changes, and consumed repeated
  history.
- Worker media E2E verifies both repeated and continuous lifecycle in the public
  Redis alert outbox.
- MVP E2E verifies the same lifecycle through REST and WebSocket contracts.
- Existing audio-loss and video-freeze E2E tests remain part of the media suite.

The accelerated publisher runs at `speed=6`. Higher speed is intentionally not
used for rule verification because it can outrun the sliding playlist window
and turn this test into an admission-overload benchmark.

## Manual verification

Generate and publish the fixture from the project root:

```powershell
python scripts/generate_monitoring_test_cases.py --reset --case black_screen
python scripts/serve_hls.py
python scripts/publish_monitoring_test_stream.py black_screen --reset --speed 1
```

Use this URL in the dashboard:

```text
http://127.0.0.1:8000/live_cases/black_screen/master.m3u8
```

Run automated media verification with Redis and FFmpeg available:

```powershell
python scripts/run_mvp_e2e.py media
```

## Definition of Done

- Generated fixture and documentation match the 60-second rule.
- Redis and WebSocket expose matching OPEN/RESOLVED event IDs.
- No quiet-window recovery remains in the black-screen production path.
- Black, audio-loss, and video-freeze regression suites pass.
- `git diff --check` and Python compilation pass.
