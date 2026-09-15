# Phase Realtime 2 - Live Micro-Batch And Per-Item Completion

## Goal

Rut ngan thoi gian mot batch giu lane cua `(profile, variant)` va xoa work da
hoan tat khoi admission queue ngay trong batch. Phase nay khong thay business
rule, detector, reducer, Redis event contract hay thu tu commit.

## Batch Policy

`ProfileScheduler` chon batch size theo resource class:

```text
metadata      20
video_decode   2
audio_decode   4
expensive      1
```

`max_segments_per_batch` cu van la gioi han tren toan cuc. Vi du video mac dinh
la `2`, nhung neu global ceiling la `1` thi batch thuc te la `1`.

Day la policy scheduling noi bo, chua duoc dua vao public stream contract. Gia
tri can duoc tuning bang benchmark truoc khi tro thanh cau hinh production.

## Per-Item Completion

Worker xu ly cac item trong micro-batch theo thu tu. Sau moi item:

- tat ca processor can thiet complete/terminal: acknowledge identity ngay;
- retryable failure, partial claim hoac analysis failure: khong acknowledge;
- callback acknowledge loi: log loi, giu co che retry o poll tiep theo;
- shutdown giua batch: item da xong van duoc acknowledge, item chua chay van
  nam lai trong queue sau khi reservation duoc release.

`finally` cua scheduler van release toan bo admission reservation va active
batch key. Release identity da acknowledge la idempotent.

## Architecture Boundary

- Scheduler so huu batch policy va admission lifecycle.
- Worker khong biet implementation cua admission queue; no chi phat completion
  callback bang `ProfileSegmentIdentity`.
- Processor/state store tiep tuc so huu claim, retry va exactly-once commit.
- Mot `(profile, variant)` van chi co mot active batch, nen temporal ordering
  khong thay doi.

Parallel analysis va ordered commit la Phase 3, khong nam trong phase nay.

## Verification

Deterministic fixture, `1x`, bon check:

```text
PASS
queue lag max: 0.013s
queue lag slope: -0.0001s/s
completed work: 21
dropped segments: 0/0/0 (warmup/steady/cooldown)
```

Overload fixture, `1.5x`, bon check:

```text
FAIL steady-state slope criterion
queue lag max: 3.052s
queue lag slope: 0.1250s/s
queue lag after cooldown: 0s
completed work: 29 (1.45 work/s)
dropped segments: 0/0/0
peak media processes: 2/4
```

Ket qua `1.5x` cho thay pipeline bao toan du lieu va hoi phuc sau overload,
nhung scheduler tuan tu van chua khai thac het process capacity. Day la baseline
de Phase 3 giai quyet, khong phai loi can che trong Phase 2.

## Definition Of Done

- resource-specific micro-batch co global safety ceiling;
- completed item khong bi giu den poll/batch sau;
- retryable item va item chua chay khong bi mat;
- stop giua batch khong bat dau item moi;
- thu tu xu ly va exactly-once boundary duoc giu nguyen;
- fixture `1x` khong drop va khong tang queue lag;
- overload result duoc luu lam baseline cho Phase 3;
- focused va full regression tests pass.
