# Phase Macroblocking 7 - Product Wiring

## Status

Complete.

## Public Configuration

Macroblocking adds one optional public check only:

```json
"macroblocking": {"enabled": false}
```

Detector confidence, analyzer geometry, sampling FPS, scales, stride, fusion,
15% affected area, and 10-second duration are not exposed through the MVP API.
Existing payloads remain valid because the check defaults to disabled.

Macroblocking requires `highest_quality` variant selection. This invariant is
enforced by `StreamConfig`, not only by the dashboard, so CLI, REST, desired
state recovery, and direct application callers cannot accidentally analyze all
variants.

## Composition

- Disabled: no macroblocking processor, no macroblocking frame branch, and no
  additional video analysis requirement.
- Enabled: `MonitoringSessionFactory` creates one macroblocking processor and
  Redis event store.
- Black screen, video freeze, and macroblocking continue to share one
  `VideoRealtimeProfile` and one FFmpeg decode pass per segment.
- Runtime status reports macroblocking as `ENABLED` or `DISABLED` with the other
  checks.

The CLI opt-in is `--enable-macroblocking`.

## Presentation

`MACROBLOCKING` is part of the public alert enum and is accepted by the strict
Redis-to-API codec, WebSocket endpoint, and dashboard. The dashboard renders:

- OPEN/RESOLVED lifecycle;
- variant label;
- average and peak affected-area percentages;
- bounded first/last segment range;
- safe HTTP(S) links with `noopener noreferrer`.

DOM content uses `textContent` and URL protocol validation. Macroblocking alert
attributes cannot inject HTML or script URLs. ES module versions were advanced
so an existing browser session does not retain the pre-macroblocking renderer.

## Architecture Boundary

Presentation imports only public DTOs/contracts. It does not import the
macroblocking detector, policy, Redis keys, repositories, or event store.
Macroblocking check modules do not import another disease implementation.

## Verification

Coverage includes schema and desired-state round trips, strict DTO parsing,
disabled/enabled composition, shared video profile behavior, highest-quality
enforcement, alert codec acceptance, frontend rendering, area summary, safe
segment URLs, and static dependency scans.
