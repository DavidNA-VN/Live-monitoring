# Phase Macroblocking 5 - Pure Temporal Reducer

## Status

Complete.

## Boundary

Phase 5 turns trusted per-segment macroblocking intervals into deterministic
canonical event transitions. It does not read or write Redis and does not run
FFmpeg. Phase 6 owns persistence, recovery state, and alert outbox delivery.

## Rules

- Spatial candidacy remains in `MacroblockingCandidateRule`: valid detector
  evidence, detector confidence threshold, and affected area at least 15%.
- A canonical event becomes alertable once its continuous wall-clock span
  reaches 10 seconds.
- A negative gap of at most `maximum_negative_gap` may be bridged. Positive
  evidence duration is tracked separately so aggregate statistics are not
  diluted by that tolerated gap.
- Sequence gaps, timeline changes, discontinuities, decode failures, and
  incomplete sampling are UNKNOWN. They close positive stitching but never
  count as healthy recovery.
- A complete segment with no positive interval emits `HEALTHY_EVIDENCE`.
  Phase 6 combines it with persisted recovery state and requires the configured
  number of full healthy segments before publishing RESOLVED.
- Event identity is deterministic from storage identity plus variant and media
  timeline coordinates. Public event payloads keep the external stream ID.
- A retried segment/media revision cannot add duration or evidence twice.

## Transition Contract

- `PERSIST_OPEN`: save canonical state; no public alert is due.
- `OPEN_ALERT`: the event crossed 10 seconds and `alert_sent` is already marked.
- `CLOSE_EVENT`: positive stitching ended; preserve this event for history and
  possible recovery of an already-open alert.
- `HEALTHY_EVIDENCE`: one full, trusted healthy segment was observed.
- `UNKNOWN_GAP`: evidence is insufficient; never resolve from this transition.
- `MARK_COMMITTED`: no additional domain mutation is needed.

Every successfully consumed segment has exactly one transition with
`commits_segment=True`. Redis commit markers and atomic alert publication remain
Phase 6 responsibilities.

## Verification

The focused suite covers inclusive 15% and 10-second boundaries, one-time OPEN,
mid-segment threshold crossing, duplicate delivery, short-gap continuity,
duration-weighted statistics, deterministic IDs, healthy recovery evidence,
sequence gaps, decode failures, and incomplete observations.
