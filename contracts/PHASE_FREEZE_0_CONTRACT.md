# Video Freeze Phase 0 Contract

## Detection semantics

`VIDEO_FREEZE` means decoded video frames remain visually unchanged according
to the configured FFmpeg `freezedetect` threshold. The baseline does not infer
whether a static scene is intentional.

## Business thresholds

```text
duration < 3 seconds        -> no public alert message
3 <= duration < 5 seconds  -> severity WARNING
duration >= 5 seconds      -> severity ALERT
```

Three warning-duration freeze events in a rolling 120-second window produce a
`REPEATED_VIDEO_FREEZE` alert.

Lifecycle state and severity are independent. Under schema version 1.0,
severity remains in the canonical string attributes map:

```json
{
  "state": "UPDATE",
  "attributes": {
    "severity": "ALERT"
  }
}
```

An event crossing the alert threshold keeps the same `event_id`:

```text
OPEN WARNING -> UPDATE ALERT -> RESOLVED
```

## Continuity semantics

`video_returned` means motion was observed. `observation_gap` closes the
current technical event because continuity is unknown; it must not be shown as
confirmed recovery.

The baseline joins edge-touching intervals across adjacent segments when
timeline generation, discontinuity sequence, media revision and sequence
identity are continuous. It does not compare boundary frame fingerprints.

## Backward compatibility

- Alert schema remains version `1.0` and adds event type enum values only.
- `video_freeze` is optional in the stream-config schema during phased rollout.
- Existing black-screen/audio-loss requests remain valid.
- Worker/config wiring is deferred until the implementation phase that can
  actually execute the check; the API must not advertise it in the UI earlier.

## Delivery boundary

Freeze publishes only through the canonical durable alert sink. Redis Pub/Sub
is not part of Phase 0. A future Stream-to-Pub/Sub relay may be added after the
outbox without changing freeze profile, processor, reducer or repository code.
