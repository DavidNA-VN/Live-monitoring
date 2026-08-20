# Live HLS Media Monitor

Service theo dõi live HLS master playlist và phát hiện black-screen gần realtime. Trạng thái xử lý, runtime health và alert outbox được lưu trong Redis; terminal alert là debug consumer tùy chọn.

## Yêu cầu

- Python 3.10+
- FFmpeg và FFprobe có trong `PATH`
- Redis 7+ (có thể chạy bằng Docker)

Kiểm tra môi trường trên PowerShell:

```powershell
python --version
ffmpeg -version
ffprobe -version
docker --version
```

## Cài đặt

```powershell
git clone https://github.com/DavidNA-VN/Live-monitoring.git
cd Live-monitoring

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Khởi động Redis

Tạo container lần đầu:

```powershell
docker run -d --name media-redis -p 6379:6379 redis:7-alpine
```

Các lần sau:

```powershell
docker start media-redis
```

Mặc định service dùng `redis://localhost:6379/0`. Có thể đổi bằng biến môi trường:

```powershell
$env:REDIS_URL="redis://localhost:6379/0"
```

## Chạy với live URL

URL đầu vào phải là HLS master playlist:

```powershell
.\.venv\Scripts\python.exe src\live_main.py `
  --url "https://example.com/live/master.m3u8" `
  --stream-id "channel-01" `
  --console
```

- Bỏ `--console` nếu chỉ muốn publish alert/health vào Redis.
- Có `--console` để hiển thị OPEN/UPDATE/RESOLVED/repeated alert trên terminal.
- Dừng bằng `Ctrl+C`; runtime sẽ graceful drain các segment đang xử lý.

## Chạy thử bằng HLS local

Đặt video `.mp4` vào `source_videos/`, sau đó tạo HLS:

```powershell
mkdir source_videos -ErrorAction SilentlyContinue
python scripts\mp4_to_hls.py
python scripts\serve_hls.py
```

Ở terminal khác, chạy monitor với URL được server in ra, ví dụ:

```powershell
.\.venv\Scripts\python.exe src\live_main.py `
  --url "http://127.0.0.1:8000/sample/master.m3u8" `
  --stream-id "local-sample" `
  --console
```

## Chạy test

```powershell
pip install pytest
$env:REDIS_TEST_URL="redis://localhost:6379/15"
python -m pytest -q -rs
```

Redis integration tests sẽ skip nếu Redis test database không truy cập được. FFmpeg integration tests yêu cầu executable trong `PATH`.

## HLS support hiện tại

- MPEG-TS segment thông thường.
- `EXT-X-BYTERANGE`.
- fMP4 với `EXT-X-MAP`.
- LL-HLS chỉ xử lý full segment, chưa xử lý part.
- Encrypted media hiện trả unsupported reason.

Không commit live URL/token, `.env`, video, segment HLS hoặc Redis data vào repository.
