# Phase Macroblocking 4 - Segment Detector And Processor

## Scope

Phase 4 converts requirement-keyed frame observations into a bounded segment
result. It adds no event lifecycle, Redis keys or alert messages.

## Detector Boundary

`MacroblockingDetector` reads only
`AnalysisRequirement.MACROBLOCKING_OBSERVATIONS` from `video_realtime`.
The default candidate boundary is:

```text
observation.valid
AND affected_area_ratio >= 0.15
AND blocking_confidence >= 0.65
```

The 15 percent threshold remains owned by `MacroblockingAlertPolicy`. The
confidence threshold and 1 fps sampling rate are benchmarked detector tuning.

## Segment Aggregation

Consecutive candidate samples become `MacroblockingInterval` values. Each
interval contains:

- start and end offsets within the segment;
- duration-weighted average and peak affected area;
- duration-weighted average and peak confidence;
- duration-weighted average boundary support.

A negative sample, invalid sample or missing sampling cadence closes the
current interval. A sample contributes at most `1 / sampling_fps` seconds and
never extends beyond the segment duration.

The result retains only the bounded observations emitted by Phase 3 and the
aggregated intervals. Segment URI, media sequence, variant and program date
time are preserved for later event and alert attribution.

## Unknown And Failure Semantics

- Failed profile analysis produces `checked=False` and preserves retryability.
- Empty, excessive or unordered observations fail defensively.
- Invalid or missing samples produce `coverage_complete=False`.
- Partial coverage is a successful processor payload, not a retry loop and not
  healthy evidence.
- Phase 5 must break temporal continuity when coverage is incomplete.

## Processor And Scheduler

`MacroblockingSegmentProcessor` uses the existing `video_realtime` profile and
requests only `MACROBLOCKING_OBSERVATIONS`. The scheduler can therefore union
its requirement with black-screen and freeze requirements and execute one
profile job per segment.

Commit targets the `MacroblockingEventStore` protocol. A concrete temporal or
Redis implementation is intentionally deferred to later phases.

## Definition Of Done

- Frame candidates aggregate into bounded weighted intervals.
- Negative, invalid and missing cadence split intervals.
- Invalid evidence cannot become a healthy segment.
- Retryable and terminal profile failures remain distinct.
- Processor declares no new analysis profile or process class.
- Segment identity and URI survive in the detection result.
