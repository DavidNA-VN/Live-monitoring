# Phase Macroblocking 2 - Pure Multi-scale Analyzer

## Scope

Phase 2 implements only spatial frame analysis. It does not integrate FFmpeg
with the live profile, create temporal events, persist Redis state or publish
alerts.

The analyzer returns `affected_area_ratio`, `blocking_confidence`,
`boundary_support_ratio`, bounded per-scale diagnostics, and validity. The
business threshold (`affected_area_ratio >= 0.15`) remains in
`MacroblockingCandidateRule`, outside the analyzer.

## Pipeline

```text
grayscale frame
  -> shared horizontal/vertical difference maps
  -> three resolution-relative overlapping window scales
  -> period/phase, boundary strength, support and texture masking
  -> overlap-safe projection per scale
  -> configurable cross-scale fusion
  -> run-length connected regions and bounded region reconstruction
  -> one affected-area ratio and confidence
```

The period search baseline is `(4, 6, 8, 12, 16)` at analysis resolution.
This is detector tuning and must be benchmarked again if analysis resolution
changes. The default fusion is intentionally not implicit: callers must select
one strategy.

## Benchmark Baseline

Local generated fixtures, 480x270 luminance, 1 fps,
`max_with_consistency` fusion:

```text
frames: 80
precision: 0.9231
recall: 1.0000
natural periodic grid candidates: 0
throughput: 1.616 fps
mean analyzer wall time: 618.6 ms/frame
```

Three isolated clean-motion frames were candidates. They did not form the
continuous 10-second sequence required by the business rule. This remains a
calibration risk and must be covered by external natural-content datasets
before production rollout.

Area-band accuracy is 0.6389 with mean distance 0.0252 from the annotated
bands. Ground truth bands are approximate spatial labels, not pixel-perfect
masks.

## Architecture Decisions

- Difference maps are calculated once per frame and shared by all scales.
- Support is first-class evidence; rejected windows do not dilute it.
- Overlap count does not inflate confidence; projection uses max union with a
  tapered edge.
- Cross-scale fusion remains configurable.
- Connected components operate on horizontal runs, not per-positive-pixel
  Python traversal.
- Uniform frames are invalid evidence, not healthy evidence.
- Raw frames, heatmaps and per-window data are not persisted.

## Definition Of Done

- Pure analyzer has no FFmpeg, HLS or Redis dependency.
- Odd dimensions, borders, uniform frames and sparse regions are tested.
- Natural-grid hard negative remains in the fixture suite.
- Business and detector thresholds remain separate.
- Throughput exceeds the selected 1 fps sampling baseline locally.
- Predictions are reproducible JSON consumable by the benchmark scorer.
- Shared video decode integration is deferred to Phase 3.
