# Phase Realtime 3 - Ordered Parallel Analysis

## Goal

Cho phep phan `profile.analyze()` cua nhieu segment chay dong thoi khi resource
phu hop, nhung processor/reducer/Redis commit van dien ra dung timeline. Phase
nay khong cho phep hai reducer commit song song trong cung `(profile, variant)`.

## Pipeline

```text
ordered admitted work
        |
        v
claim processors theo timeline
        |
        v
bounded ANALYZE window
        |
        v
ordered result queue
        |
        v
PROCESS + COMMIT tung item theo timeline
```

Moi buffered result giu day du `ProfileSegmentWork`, gom admission identity,
segment va processing identity cua tung processor. Scheduler da sort theo:

```text
timeline_generation
discontinuity_sequence
sequence
media_revision
```

Result N+1 co the analyze xong truoc N, nhung `future.result()` chi duoc consume
tu dau ordered queue. Vi vay reducer khong nhin thay completion order cua
executor.

## Bounded State

- ordered result queue bi gioi han boi `parallel_analysis_by_resource`;
- batch size Phase 2 van la hard upper bound;
- khong co danh sach result tang vo han;
- lease heartbeat tiep tuc chay trong luc result doi ordered commit;
- profile timeout hien co la deadline cua analysis (video/audio mac dinh co
  timeout tai profile), nen ordered cursor khong doi vo han.

## Retry And Shutdown

- N retryable: processor do bi block trong phan con lai cua batch;
- N+1 da analyze nhung chua commit: claim cua processor bi block duoc relinquish
  atomic, hoan lai attempt va khong commit vuot N;
- processor khac trong cung item van co the commit theo thu tu;
- shutdown khong submit item moi;
- analysis da in-flight duoc drain, claim chua commit duoc tra retryable;
- admission reservation luon release trong `finally` cua scheduler.

## Resource Policy

Preset sau benchmark tren may hien tai:

```text
metadata       1
video_decode   1
audio_decode   2
expensive      1
```

Day khong phai gioi han kien truc. Mapping co the inject vao `ProfileScheduler`
de benchmark/deploy theo tung loai may.

Video `2` khong duoc chon lam default vi FFmpeg video da tu dung nhieu thread va
macroblocking con co Python CPU work. Benchmark `1.5x` voi video concurrency `2`
cho ket qua:

```text
FAIL
queue slope: 0.3250s/s
lag max: 7.085s
lag after cooldown: 10.102s
throughput: 1.25 work/s
video execution p95: 5.734s
peak media processes: 3
dropped segments: 0
```

Preset video `1`, audio `2` duoc lap lai hai lan cung fixture `1.5x`, bon check:

```text
run 1: PASS, lag 0s, throughput 1.60 work/s, dropped 0
run 2: PASS, lag 0s, throughput 1.50 work/s, dropped 0
```

Ket luan: ordered parallel capability da co, nhung song song phai ap theo cost
cua resource/profile; tang FFmpeg video process khong mac dinh dong nghia tang
throughput.

## Architecture Boundary

- Scheduler so huu policy batch va parallelism theo resource.
- Worker so huu claim, analysis window va ordered commit.
- Processor/reducer khong biet va khong phu thuoc executor completion order.
- State store van la exactly-once boundary cho tung check/segment.
- State store so huu Lua relinquish operation; worker khong tu sua Redis state.
- Process gate van la hard ceiling cua toan stream/service.

## Definition Of Done

- analysis completion co the dao thu tu, commit van deterministic;
- ordered buffer bounded va configurable theo resource;
- retry cua N khong lam N+1 commit vuot hoac bien mat;
- ordered deferral khong tieu ton retry attempt cua N+1;
- shutdown giua ordered wait khong deadlock va khong bo quen claim;
- controlled test quan sat peak analysis bang `2`;
- production preset duoc chon theo benchmark, khong theo worker count;
- fixture overload lap lai zero lag va zero drop;
- domain, full regression va Redis integration tests pass.
