# Phase Benchmark 0 - Workload And SLO Baseline

## 1. Goal

Khoa workload va cach danh gia truoc khi them instrumentation hoac tuning.
Baseline MVP chi chung nhan mot live HLS stream va mot video variant co chat
luong cao nhat. Alert presentation va segment URL khong nam trong phase nay.

## 2. Certified workload target

```text
stream count:             1
video selection:          highest_quality
video variants analyzed:  1
audio renditions:          0 neu audio muxed, toi da 1 neu external audio
enabled checks:            black_screen + video_freeze + audio_loss
service level:             realtime protected with observable coverage
```

`highest_quality` xep hang video bang:

1. So pixel `width * height` cao nhat.
2. Neu cung do phan giai, `BANDWIDTH` cao nhat.
3. Neu thieu `RESOLUTION`, fallback sang `BANDWIDTH`.
4. Neu van bang nhau, dung stable ID de ket qua deterministic.

Neu video tham chieu external audio group, chi chon mot rendition: `DEFAULT`
truoc, `AUTOSELECT` sau, cuoi cung la rendition dau tien trong manifest. Cac
audio language/track khac khong thuoc baseline MVP.

## 3. Measurement units

- `media segment`: mot segment cua rendition da chon.
- `profile-work`: mot media segment nhan mot video hoac audio profile.
- `detector result`: black va freeze co the cung sinh tu mot video profile-work.
- `input rate`: tong profile-work moi giay.
- `throughput`: profile-work hoan thanh moi giay.
- `queue slope`: toc do queue tang hoac giam trong steady state.
- `live-edge lag`: khoang cach tu media dang xu ly den live edge.
- `detection latency`: thoi diem alert duoc emit tru thoi diem loi xay ra.

Voi audio muxed va segment duration `T`:

```text
input rate = (1 video profile + 1 audio profile) / T
```

Vi du `T = 2s` tao xap xi mot profile-work moi giay.

## 4. Provisional pass criteria

Mot workload chi duoc coi la nam trong capacity cua may khi het warm-up:

- queue slope `<= 0` trong steady state;
- queue lag va live-edge lag khong tang vo han;
- `dropped_media_segment_count = 0`;
- khong co analysis timeout/failure bat thuong;
- expected fixture events van du va dung lifecycle;
- CPU steady state muc tieu `<= 80%` de con safety margin;
- RAM va so FFmpeg process khong tang dan theo thoi gian.

Detection latency target chua khoa thanh mot con so o Phase 0. Phase 1 phai do
du du lieu truoc khi chot p95 SLO.

## 5. Benchmark protocol

Moi cau hinh gom ba khoang:

```text
warm-up:       2 minutes
steady state: 10 minutes
cooldown:      1 minute
```

Local deterministic fixture la regression source. External CDN stream chi la
representative smoke/capacity input vi network, token va origin co the thay doi.
Moi run phai ghi:

- run ID va UTC start/end;
- source type, khong commit signed external URL;
- codec, resolution, bandwidth, frame rate va target duration;
- enabled checks va selected rendition;
- video/audio worker limits va media process gates;
- input rate, throughput, queue depth/lag/slope va live-edge lag;
- dropped media, timeout, failure, CPU, RAM va peak FFmpeg processes.

## 6. Baseline host

```text
OS:       Windows 11 Home 64-bit, 10.0.26200
CPU:      AMD Ryzen 7 8745H, 8 cores / 16 logical processors
RAM:      15.31 GiB usable
GPU:      NVIDIA GeForce RTX 4050 Laptop GPU (4 GiB reported)
iGPU:     AMD Radeon 780M
Python:   3.13.9
FFmpeg:   8.1.2 full build (Gyan.dev)
```

GPU hien chua duoc baseline pipeline su dung. Khong duoc gan ket qua CPU decode
voi GPU capacity khi chua co benchmark rieng.

## 7. Non-goals

Phase nay khong:

- thay business rule black, freeze hoac audio-loss;
- thay alert payload, WebSocket hoac Dashboard incident presentation;
- tang process/worker default de lam dep ket qua;
- benchmark 20 streams;
- them detector moi;
- dung AI/GPU khi chua co CPU baseline;
- claim production capacity tu mot external smoke run.

## 8. Pre-instrumentation smoke result

Command:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_detection_profiles.py --iterations 3
```

Fixture hien tai co do phan giai `160x90`, 15 segment samples va 30 giay media:

| Mode | Wall seconds | Realtime factor | Failures |
|---|---:|---:|---:|
| black | 0.665 | 0.0222 | 0 |
| freeze | 0.615 | 0.0205 | 0 |
| black + freeze | 0.623 | 0.0208 | 0 |
| black + freeze + audio | 1.095 | 0.0365 | 0 |

Ket qua nay chi xac nhan shared video profile khong tao decode pass thu hai va
audio la mot profile pass rieng. No khong dai dien 1080p, network/CDN, queue,
concurrency hoac capacity stream/node.

## 9. Definition of Done

- `highest_quality` co contract dong bo tai domain, CLI, API va JSON schema;
- default MVP chi fetch video variant cao nhat va toi da mot external audio;
- selection deterministic va co test cho missing resolution/tie/audio group;
- workload, measurement units, protocol va provisional pass criteria duoc khoa;
- full regression pass, khong thay detection result;
- Phase 1 co the bo sung timing instrumentation ma khong doi contract Phase 0.
