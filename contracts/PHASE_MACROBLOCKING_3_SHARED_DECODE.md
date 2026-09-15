# Phase Macroblocking 3 - Shared Video Decode Integration

## Scope

Phase 3 transports sampled luminance frames from the existing
`VideoRealtimeProfile` into the pure macroblocking analyzer. It does not add a
segment detector, processor, temporal reducer, Redis state or alerts.

## Requirement Contract

`AnalysisRequirement.MACROBLOCKING_OBSERVATIONS` identifies the new output.
`VideoRealtimeProfile` advertises it only when `enable_macroblocking=True`.
When disabled, existing black-screen and freeze commands do not include an
extra filter branch or raw-frame output.

## One Decode Rule

When black-screen, freeze and macroblocking are requested together, the command
contains one FFmpeg executable, one media input and one decoded-video split:

```text
decoded video
  -> blackdetect + freezedetect metadata sink
  -> 32x32 boundary samples for freeze continuity
  -> fps + normalized scale + gray macroblocking samples
```

Adding macroblocking therefore adds filter and raw-frame work, but does not
spawn a second decode process for the segment.

## Bounded Workspace

`MacroblockingSampleWorkspace` owns one temporary raw luminance file. The
profile closes it through `ExitStack` on success, non-zero FFmpeg exit, process
start failure, timeout, media input failure and parser failure.

The output is bounded twice:

- FFmpeg receives `-frames:v ceil(segment_duration * sampling_fps) + 2` for
  the macroblocking output;
- the reader rejects empty, truncated or oversized raw output.

Each complete frame is reshaped strictly to the configured analysis width and
height. Raw frame bytes and heatmaps never enter Redis or public contracts.

## Failure Semantics

An empty, truncated or oversized sample makes `VideoRealtimeAnalysis.checked`
false. It is not converted into a healthy observation. Phase 4 processors must
therefore propagate this as UNKNOWN evidence.

## Compatibility

- Existing black-only command shape remains unchanged when macroblocking is
  disabled.
- Freeze still receives its original 32x32 boundary fingerprints.
- Black and freeze parser outputs are unchanged when the third branch is on.
- Requirement-keyed outputs prevent macroblocking evidence from leaking into
  another detector contract.

## Definition Of Done

- One FFmpeg process and one input produce all three analysis outputs.
- Macroblocking-only analysis is supported without dummy metadata filters.
- Disabled macroblocking adds no raw-frame I/O.
- Strict frame parsing and bounded frame count are tested.
- Temporary files are removed after normal completion and timeout.
- A real FFmpeg integration test exercises metadata and macroblocking outputs.
