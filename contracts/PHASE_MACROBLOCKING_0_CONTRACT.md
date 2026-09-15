# Phase Macroblocking 0 - Contract and Architecture Decision

Status: Complete.

## Scope

Phase 0 defines domain boundaries only. It does not alter FFmpeg commands,
scheduling, Redis, public API or UI.

## Detection and business boundary

The analyzer only reports `affected_area_ratio`, `blocking_confidence`,
`boundary_support_ratio` and optional per-scale debug evidence. It never applies
the 15 percent threshold.

Candidate classification combines three independent conditions:

```text
valid observation
AND detector confidence threshold
AND business affected-area threshold
```

Changing the business threshold from 15 to 20 percent must not change
difference maps, window scoring or scale fusion.

## Support and fusion

Boundary support is first-class evidence:

```text
support_ratio = suspicious candidate-grid boundaries / candidate boundaries
```

No production fusion default is selected in Phase 0. Fusion remains replaceable
and fixtures will compare max, max-with-consistency and top-two strategies.

## Ground truth

Positive fixture labels require a time range and approximate spatial area band.
Pixel-perfect masks are optional. Boolean-only labels are insufficient because
an incorrect area estimate can cross the 15 percent business threshold.

## One-decode ADR

Macroblocking becomes another requirement of `VideoRealtimeProfile`. When
requested, the existing FFmpeg graph gains a sampled grayscale branch. It must
not create a separate macroblocking decode profile or FFmpeg process. Disabled
macroblocking must add no sampled-frame I/O.

The initial transport may use a bounded temporary rawvideo file because the
current `ProcessRunner` captures text stdout/stderr. A binary pipe may later
replace transport inside the profile without changing downstream contracts.

## Persistence and UNKNOWN

Per-frame/per-scale evidence is ephemeral. Redis may later persist only bounded
summaries and affected segment range/URI, never raw frames, heatmaps or an
unbounded observation list.

Invalid observations require a reason. Decode failure, truncated output,
segment gaps and queue drops are UNKNOWN rather than healthy. Baseline recovery
requires one fully observed healthy media segment.

## Definition of Done

- Frame, scale, segment and bounded event contracts exist.
- Support ratio is explicit rather than hidden inside confidence.
- Analyzer output does not apply the 15 percent threshold.
- Candidate tests detector confidence and business area independently.
- The 15 percent and 10 second boundaries are inclusive and tested.
- Invalid observations cannot silently represent healthy frames.
- Shared-decode and persistence decisions are documented.
