# Phase Macroblocking 6 - Redis Persistence and Alert Lifecycle

## Status

Complete.

## Ownership

Macroblocking owns its Redis keyspace under:

```text
{prefix}:stream:{storage_id}:check:macroblocking:variant:{variant_stable_id}:...
```

Keys cover one canonical open event, bounded event details, one pending alert
recovery, one short-lived variant lock, and timeline/revision-aware commit
markers. No macroblocking key was added to the shared core key registry.

## Atomic Lifecycle

For every segment, `RedisMacroblockingEventStore`:

1. checks the timeline/revision-aware commit marker;
2. acquires the per-variant owned lock;
3. loads canonical and recovery state;
4. runs the pure Phase 5 reducer;
5. queues state, metrics, OPEN/RESOLVED outbox entries, and the commit marker in
   one Redis transaction;
6. releases only the lock token it owns.

A crash before `EXEC` changes nothing. A retry after `EXEC` sees the commit
marker. Competing workers cannot publish duplicate alerts.

## Recovery Semantics

- A continuous event reaching 10 seconds publishes one `OPEN`.
- Closing an alerted event creates pending recovery state.
- UNKNOWN resets healthy evidence and never resolves an alert.
- One complete healthy segment publishes `RESOLVED` with the same event ID.
- If positive evidence returns while an earlier alert is unresolved, canonical
  evidence is still stored but another OPEN for that variant is suppressed.
- Timeline changes and reused media sequence numbers have independent event and
  commit identities and cannot overwrite historical details.

## Public and Private Identity

Redis keys, locks, commit markers, and deterministic alert IDs use `storage_id`.
Alert envelopes use `external_stream_id`; storage identity is never serialized
into the public payload.

## Bounded State

Persisted event state contains only aggregate duration, area, confidence,
support, timeline coordinates, segment range, and first/last segment URI.
Frames, per-frame observations, scale evidence, and heatmaps are excluded.
Event/recovery keys and commit markers have TTLs; the shared alert outbox has a
configured maximum length.

## Metrics

Runtime metrics include analysis, invalid result, invalid observation,
candidate interval, observation gap, OPEN, RESOLVED, and accumulated alert
latency. They share the existing runtime metrics hash and TTL contract.

## Verification

Tests cover strict codec round trips, bounded payloads, restart recovery,
duplicate delivery, owned-lock contention, UNKNOWN recovery behavior,
timeline/variant isolation, public identity, segment URIs, and runtime metrics.
