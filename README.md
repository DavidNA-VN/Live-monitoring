# Live HLS Media Monitor

Hệ thống giám sát chất lượng luồng video **HLS trực tiếp (Live HLS)** theo thời gian thực (near real-time), tự động phát hiện các bất thường về hình ảnh và âm thanh, cung cấp bảng điều khiển (Web Dashboard), REST API, WebSocket và cơ chế phân phối cảnh báo qua Redis.

---

## 📌 Tính năng nổi bật

### 1. Giám sát đa dạng lỗi phát sóng (Anomaly Detection)
- **Black Screen (Màn hình đen)**: Phát hiện màn hình đen liên tục hoặc chớp nháy lặp lại (repeated blackout) theo ngưỡng thời gian và luminance (`blackdetect`).
- **Audio Loss / Silence (Mất tiếng / Khoảng lặng)**: Phát hiện suy giảm âm lượng nghiêm trọng hoặc tắt tiếng liên tục trên các track âm thanh muxed (`silencedetect`).
- **Video Freeze (Đứng hình)**: Phát hiện khung hình bị treo hoặc lặp lại liên tục với cơ chế lọc nhiễu khung hình (`freezedetect`).
- **Macroblocking (Vỡ hình / Khảm hạt)**: Thuật toán phân tích cấu trúc lưới điểm ảnh đa tỷ lệ (Multi-scale Grid Score, Difference Maps, Window Scale Fusion) để phát hiện vỡ hình do nén hoặc mất gói tin mạng.

### 2. Quản lý vòng đời cảnh báo (Alert Lifecycle & State Machine)
- Quản lý trạng thái sự cố chặt chẽ: `OPEN` (bắt đầu lỗi) ➔ `UPDATE` (cập nhật diễn biến) ➔ `RESOLVED` (hồi phục bình thường).
- Cơ chế **Repeated Alerts** cảnh báo các lỗi chớp nháy gián đoạn mà không tạo alert bão hòa.
- Lưu trữ Outbox và Event Stream độc lập trên **Redis Streams**, đảm bảo tính bất biến và không thất thoát sự kiện.

### 3. Tối ưu hóa hiệu năng & Pipeline phân tích
- **Shared Decode Pipeline**: Phân tích đồng thời nhiều loại lỗi hình ảnh (Black Screen, Video Freeze, Macroblocking) trên một lần giải mã duy nhất, giảm tải CPU/RAM tối đa.
- **Process Resource Budgeting (`MediaProcessBudget`)**: Giới hạn số lượng tiến trình FFmpeg chạy đồng thời ở cả cấp độ luồng lẫn cấp độ toàn service, ngăn ngừa nghẽn tài nguyên.
- **Adaptive Catch-Up Admission Mode**: Tự động cân bằng giữa độ trễ phát sóng (live-edge lag) và độ bao phủ kiểm tra khi mạng chậm hoặc origin trả segment dồn toa.

### 4. Kiến trúc Microservice & Điều khiển tập trung
- **Monitoring Worker**: Daemon worker xử lý background độc lập, hỗ trợ Desired State Recovery (tự khôi phục các luồng khi worker khởi động lại) và Worker Heartbeat.
- **Presentation API (FastAPI)**: RESTful API chuẩn hóa theo JSON Schema DTO, hỗ trợ WebSocket đẩy cảnh báo trực tiếp theo từng stream.
- **Realtime Web Dashboard**: Giao diện Dark Mode + Glassmorphism hiện đại, tích hợp trình phát video trực tiếp HLS.js, thanh đo Telemetry (FPS, Bitrate, Resolution) và Event Log trực quan.

---

## 🛠 Yêu cầu môi trường

- **Python**: 3.10 trở lên (khuyến nghị 3.11 hoặc 3.12/3.13)
- **FFmpeg & FFprobe**: Đã cài đặt và có trong biến môi trường `PATH`
- **Redis**: Phiên bản 7.0+ (khuyến nghị chạy bằng Docker)

Kiểm tra trên terminal:

```powershell
python --version
ffmpeg -version
ffprobe -version
docker --version
```

---

## 🚀 Cài đặt & Khởi chạy

### 1. Clone mã nguồn & Cài đặt thư viện

```powershell
git clone https://github.com/DavidNA-VN/Live-monitoring.git
cd Live-monitoring

# Tạo và kích hoạt môi trường ảo
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Cập nhật pip và cài đặt dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Khởi chạy Redis

Chạy Redis 7 bằng Docker:

```powershell
# Tạo container lần đầu
docker run -d --name media-redis -p 6379:6379 redis:7-alpine

# Các lần sau chỉ cần start lại
docker start media-redis
```

*Mặc định service sử dụng `redis://localhost:6379/0`. Bạn có thể thay đổi thông qua biến môi trường `$env:REDIS_URL`.*

---

## 💻 Hướng dẫn vận hành

### Mô hình 1: Chạy Full-Stack (Web Dashboard + Redis Worker) — *Khuyến nghị*

Mô hình hoàn chỉnh mô phỏng môi trường thực tế: Giao diện web gửi lệnh qua API, API lưu vào Redis Gateway, Worker tiêu thụ lệnh và tiến hành phân tích HLS.

**Bước 1: Khởi động FastAPI & Web Dashboard (Terminal 1)**
```powershell
$env:MONITORING_API_MODE="redis"
$env:REDIS_URL="redis://localhost:6379/0"
python -m uvicorn src.presentation.api.main:app --host 0.0.0.0 --port 8080 --reload
```

**Bước 2: Khởi động Monitoring Worker Daemon (Terminal 2)**
```powershell
.\.venv\Scripts\python.exe src\live_main.py `
  --command-worker `
  --enable-video-freeze `
  --enable-macroblocking `
  --worker-id "worker-01"
```

**Bước 3: Trải nghiệm trên Dashboard**
1. Truy cập trình duyệt: `http://localhost:8080`.
2. Nhập `Stream ID` (ví dụ: `channel-01`) và `Master URL (.m3u8)`.
3. Tích chọn các bộ kiểm tra mong muốn (`Freeze`, `Macroblocking`).
4. Bấm **Connect** để phát video và theo dõi các cảnh báo trực tiếp từ Event Log.

---

### Mô hình 2: Chạy Web Dashboard độc lập (Chế độ `fake` Demo)

Phù hợp để kiểm thử giao diện frontend và luồng WebSocket mà không cần cài Redis hay chạy Worker phân tích:

```powershell
$env:MONITORING_API_MODE="fake"
$env:ENABLE_FAKE_GENERATOR="true"
python -m uvicorn src.presentation.api.main:app --host 0.0.0.0 --port 8080 --reload
```
*Truy cập `http://localhost:8080`, hệ thống sẽ tự động phát sinh các cảnh báo giả lập sau khi kết nối.*

---

### Mô hình 3: Chạy trực tiếp từ CLI (Standalone Runner)

Phù hợp cho kiểm tra nhanh một luồng HLS cụ thể và in log cảnh báo ra terminal:

```powershell
.\.venv\Scripts\python.exe src\live_main.py `
  --url "https://example.com/live/master.m3u8" `
  --stream-id "channel-01" `
  --console `
  --enable-video-freeze `
  --enable-macroblocking
```

- `--console`: In trực tiếp các sự kiện `OPEN`, `UPDATE`, `RESOLVED` ra màn hình dòng lệnh.
- Nhấn `Ctrl+C`: Hệ thống sẽ graceful shutdown, hoàn tất nốt các segment đang decode dở trước khi thoát.

---

### Mô hình 4: Kiểm thử với HLS giả lập nội bộ (Local Fixture)

Nếu chưa có luồng live ngoài Internet, bạn có thể tạo luồng HLS từ một video `.mp4` có sẵn:

```powershell
# 1. Đặt video nguồn vào source_videos/
mkdir source_videos -ErrorAction SilentlyContinue

# 2. Cắt video thành HLS
python scripts\mp4_to_hls.py

# 3. Phục vụ HLS qua HTTP server
python scripts\serve_hls.py
```

Ở một terminal khác, kết nối worker hoặc dashboard tới URL local vừa sinh (ví dụ: `http://127.0.0.1:8000/sample/master.m3u8`).

---

## ⚙️ Bảng tham số cấu hình chính (CLI Arguments)

| Tham số | Mặc định | Ý nghĩa |
| :--- | :---: | :--- |
| `--url` | `None` | URL HLS Master Playlist khởi tạo ban đầu |
| `--stream-id` | `None` | Định danh định tuyến nghiệp vụ của luồng (Business Identity) |
| `--command-worker` | `False` | Bật chế độ daemon nhận lệnh điều khiển từ Redis Gateway |
| `--disable-black-screen` | `False` | Tắt bộ phát hiện màn hình đen |
| `--disable-audio-loss` | `False` | Tắt bộ phát hiện mất âm thanh |
| `--enable-video-freeze` | `False` | Bật bộ phát hiện đứng hình |
| `--enable-macroblocking` | `False` | Bật bộ phát hiện vỡ khối (macroblocking) |
| `--freeze-noise-db` | `-60.0` | Ngưỡng dung sai nhiễu đóng băng hình ảnh (dB) |
| `--freeze-alert-duration` | `60.0` | Thời gian đứng hình liên tục tối thiểu để kích hoạt alert (giây) |
| `--silence-threshold-dbfs`| `-60.0` | Ngưỡng âm lượng tối đa xem là im lặng (dBFS) |
| `--audio-loss-duration` | `30.0` | Thời gian im lặng liên tục để kích hoạt alert (giây) |
| `--max-media-processes` | `4` | Giới hạn số tiến trình FFmpeg chạy đồng thời cho mỗi luồng |
| `--max-service-media-processes` | `8` | Giới hạn số tiến trình FFmpeg chạy đồng thời toàn bộ worker |
| `--startup-mode` | `bounded_history`| Cơ chế nhận diện segment khi khởi động (`bounded_history` hoặc `full_snapshot`) |
| `--startup-lookback-segments` | `4` | Số lượng segment mới nhất nạp vào khi mới khởi động |
| `--variant-selection` | `highest_quality`| Chiến lược chọn profile biến thể (`highest_quality`, `all`, `representative`, `explicit`) |

---

## 📡 API & WebSocket Contracts

Tầng Presentation tuân thủ các schema định dạng dữ liệu trong thư mục `contracts/`:

- **POST** `/api/v1/streams/{stream_id}/start`: Bắt đầu giám sát luồng với cấu hình `StreamConfigDTO`.
- **POST** `/api/v1/streams/{stream_id}/stop`: Dừng giám sát luồng.
- **POST** `/api/v1/streams/{stream_id}/pause` / `resume`: Tạm dừng hoặc tiếp tục giám sát.
- **GET** `/api/v1/streams/{stream_id}/status`: Đọc trạng thái runtime (`RuntimeStatusDTO`), bao gồm sức khỏe worker, active variants, queue lag.
- **GET** `/api/v1/streams/{stream_id}/events`: Đọc lịch sử cảnh báo gần nhất (`AlertDTO[]`).
- **GET** `/api/v1/commands/{command_id}`: Kiểm tra tiến độ và kết quả thực thi lệnh (`CommandResultDTO`).
- **WebSocket** `/api/v1/ws/streams/{stream_id}`: Kênh nhận thông điệp cảnh báo thời gian thực (`AlertMessageDTO`).

---

## 🧪 Kiểm thử (Testing)

Chạy bộ unit test và integration test với `pytest`:

```powershell
# Chạy toàn bộ test
.venv\Scripts\python.exe -m pytest -q -rs

# Kiểm tra riêng từng module
.venv\Scripts\python.exe -m pytest tests/core/test_phase2_identity_boundary.py
.venv\Scripts\python.exe -m pytest tests/detectors/test_macroblocking_analyzer.py
```

*Lưu ý: Các bài kiểm thử tích hợp Redis yêu cầu Redis đang chạy (hoặc cấu hình qua biến `REDIS_TEST_URL`). Các bài kiểm thử FFmpeg yêu cầu FFmpeg khả dụng trong môi trường.*

---

## 📂 Cấu trúc mã nguồn

```text
media-monitor/
├── contracts/               # JSON Schema chuẩn hóa cho API, Command, Alert & Status
├── scripts/                 # Bộ script hỗ trợ (tạo fixture, benchmark, publish HLS)
├── src/
│   ├── app/                 # Worker runtime, command consumers, state recovery & heartbeats
│   ├── checks/              # Quản lý sự kiện & reducer từng loại lỗi (black_screen, freeze, macroblocking, audio_loss)
│   ├── core/                # Playlist parsing, admission policy, scheduling, shared decode, Redis client
│   ├── detectors/           # Thuật toán phân tích FFmpeg/NumPy cho từng loại lỗi
│   ├── models/              # Dataclass & Entity models nội bộ
│   ├── presentation/        # FastAPI REST/WebSocket endpoints & Web Dashboard UI
│   │   ├── api/             # API routes, composition, dependency injection, settings
│   │   └── static/          # Dashboard frontend (HTML, CSS, JS)
│   └── live_main.py         # Entrypoint chính cho CLI Runner và Worker Daemon
├── tests/                   # Bộ test tự động (Unit, Integration, E2E)
├── requirements.txt         # Danh sách gói thư viện Python
└── README.md                # Tài liệu hướng dẫn dự án
```

---

## 🛡 Nguyên tắc vận hành Production

1. **Tính bất biến của Segment (Immutable Segments)**: Hệ thống sử dụng metadata trên manifest (`media_revision` fingerprint) để xác định danh tính phân tích của segment nhằm tối ưu hoá I/O, không tải trước hay băm binary bytes tại admission layer. Origin/CDN phục vụ luồng live cần tuân thủ quy chuẩn segment đã phát hành là bất biến.
2. **Quản lý Process An toàn**: Luôn đặt ngưỡng `--max-media-processes` và `--max-service-media-processes` phù hợp với số nhân CPU của máy chủ để tránh cạn kiệt tài nguyên hệ điều hành.
3. **Bảo mật**: Không commit live URLs chứa token bảo mật, file cấu hình `.env`, video dung lượng lớn hoặc dữ liệu Redis dump vào kho mã nguồn.
