# Video Freeze Phase 4 Verification

## Rule under test

- Continuous VIDEO_FREEZE opens at 60 seconds, without WARNING/UPDATE.
- Repeated freeze opens after three closed candidates in 120 seconds.
- Candidate duration is at least one reference segment and below 60 seconds.
- OPEN resolves only after one complete healthy segment.
- Black overlap is excluded from effective freeze.
- Cross-segment continuation requires matching boundary fingerprints.

## Deterministic fixtures

- `freeze_59s`: no continuous alert.
- `freeze_60s`: VIDEO_FREEZE OPEN then RESOLVED.
- `freeze_cross_three_segments`: one canonical event across boundaries.
- `three_short_freezes`: REPEATED_VIDEO_FREEZE OPEN then RESOLVED.
- `video_freeze` monitoring case: three 4s candidates and one 62s event.

Generate fixtures:

```powershell
python scripts/generate_video_freeze_fixtures.py --reset
python scripts/generate_monitoring_test_cases.py --reset --case video_freeze
```

## Verification commands

Targeted domain and persistence:

```powershell
python -m pytest tests/checks/video_freeze tests/policies/test_video_freeze_policy.py tests/integration/test_video_freeze_redis.py -q
```

Worker media E2E requires Redis, FFmpeg and FFprobe:

```powershell
$env:RUN_WORKER_MEDIA_E2E="1"
$env:REQUIRE_MVP_E2E="1"
python -m pytest tests/e2e/test_video_freeze_live_e2e.py -v
```

Shared profile benchmark:

```powershell
python scripts/benchmark_detection_profiles.py --iterations 3 --mode all
```

MAE threshold benchmark with labeled pairs:

```powershell
python scripts/benchmark_freeze_boundary_mae.py path/to/labeled-pairs.json
```

Do not treat skipped Redis/media tests as a Phase 4 pass. Final closure requires
the two worker media scenarios to execute and pass against a disposable Redis DB.

## Final local result

- Targeted Phase 4: 82 passed, 7 skipped.
- Full suite with Redis: 798 passed, 7 skipped.
- FFmpeg fixture integration: passed.
- Worker media E2E: 2 passed in 47.83s.
- The low-resolution benchmark is a regression baseline, not a 1080p capacity
  claim or production SLO.
