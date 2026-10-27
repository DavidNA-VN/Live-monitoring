# Hướng dẫn demo

Chạy các lệnh tại thư mục gốc vừa giải nén. Mỗi khối terminal cần kích hoạt môi trường bằng `.\.venv\Scripts\Activate.ps1`.

## 1. Redis

Nếu máy chưa có container Redis:

```powershell
docker run -d --name media-redis -p 6379:6379 redis:8
```

Nếu đã có container tên này:

```powershell
docker start media-redis
```

Kiểm tra:

```powershell
docker exec media-redis redis-cli ping
```

Cần nhận `PONG`.

## 2. Tạo fixture một lần

```powershell
python .\scripts\generate_monitoring_test_cases.py --reset
```

FFmpeg sinh sample vào `hls_output/monitoring_test_cases`. `--reset` thay thế toàn bộ thư mục fixture do script tạo, không chạy khi publisher đang dùng fixture đó.

## 3. Terminal worker

```powershell
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
python .\src\live_main.py --command-worker --worker-id worker-demo-01 --max-streams 1 --redis-prefix media-monitor:mentor-demo
```

Chỉ chạy một worker với ID này. Trong chế độ command-worker, UI gửi cấu hình bật các bệnh theo từng stream.

## 4. Terminal API

```powershell
$env:MONITORING_API_MODE = "redis"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:REDIS_PREFIX = "media-monitor:mentor-demo"
$env:ENABLE_FAKE_GENERATOR = "false"
python -m uvicorn presentation.api.main:app --app-dir src --host 127.0.0.1 --port 8080
```

Worker và API phải dùng cùng Redis URL và prefix. Dừng API cũ trước khi mở lại cổng 8080.

Kiểm tra từ terminal khác:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health/ready
```

Cần thấy `mode=redis` và `status=ready`.

## 5. Terminal HTTP origin

```powershell
python .\scripts\serve_hls.py
```

## 6. Terminal publisher

Chọn một lệnh, không chạy toàn bộ thành một batch:

```powershell
python .\scripts\publish_monitoring_test_stream.py black_screen --window-size 48 --retention-segments 64 --reset
python .\scripts\publish_monitoring_test_stream.py video_freeze --window-size 48 --retention-segments 64 --reset
python .\scripts\publish_monitoring_test_stream.py audio_silence --window-size 48 --retention-segments 64 --reset
python .\scripts\publish_monitoring_test_stream.py audio_missing --window-size 48 --retention-segments 64 --reset
python .\scripts\publish_monitoring_test_stream.py healthy --window-size 48 --retention-segments 64 --reset
```

## 7. Dashboard

Mở http://127.0.0.1:8080 và xác nhận MODE: REDIS.

| Case | URL HLS | Alert nội dung kỳ vọng cho một vòng đầy đủ |
|---|---|---|
| black_screen | http://127.0.0.1:8000/live_cases/black_screen/master.m3u8 | 4: repeated và continuous OPEN/RESOLVED |
| video_freeze | http://127.0.0.1:8000/live_cases/video_freeze/master.m3u8 | 4: repeated và continuous OPEN/RESOLVED |
| audio_silence | http://127.0.0.1:8000/live_cases/audio_silence/master.m3u8 | 2: OPEN/RESOLVED |
| audio_missing | http://127.0.0.1:8000/live_cases/audio_missing/master.m3u8 | OPEN, chưa có âm thanh phục hồi |
| healthy | http://127.0.0.1:8000/live_cases/healthy/master.m3u8 | 0 |

Bật Freeze khi kiểm tra freeze. Tắt Macroblocking cho demo ba bệnh chính. Nhập stream ID riêng như `mentor-black-01`, rồi Connect ngay sau khi bắt đầu publisher.

Black và freeze có event continuous khoảng 62 giây trong sample 96 giây, cần quan sát đủ thời gian. Các tổng alert không tính RUNTIME_HEALTH. Publisher mặc định lặp lại nên chạy nhiều vòng sẽ có thêm alert.

Đổi case: Stop trên UI, chờ xác nhận, Ctrl+C publisher, chạy case mới, nhập URL và stream ID mới rồi Connect.

Đóng demo: Stop trên UI, Ctrl+C worker/API/publisher/HTTP origin, dừng Redis cuối cùng. Dừng Docker trước worker sẽ gây Redis timeout.

## 8. Kiểm thử

```powershell
python -m pytest -q
python .\scripts\run_worker_e2e.py all
python .\scripts\run_mvp_e2e.py all
python .\scripts\verify_monitoring_test_cases.py --redis-prefix media-monitor:mentor-verification --output .benchmark/verification.json
```

Pytest mặc định có một số media test opt-in. Để chạy chúng:

```powershell
$env:RUN_WORKER_MEDIA_E2E = "1"
$env:RUN_PHASE6_LIVE = "1"
python -m pytest tests/e2e tests/integration/test_audio_loss_live_hls.py -q
```

Các bài Redis integration dùng database test riêng, mặc định DB 15 với namespace duy nhất. Không trỏ REDIS_TEST_URL tới một Redis không dành cho kiểm thử.

Các script fixture vẫn có case macroblocking/combined để kiểm tra baseline dùng chung. Đây là detector live dạng lưới của bản demo, không chứa nhánh nghiên cứu offline.

`verify_monitoring_test_cases.py` của release này cần HTTP origin đã chạy bằng `serve_hls.py` ở terminal riêng. HTTP origin phải phục vụ `hls_output` của chính thư mục release đang kiểm tra. Nếu dùng cổng khác, truyền `--public-base-url http://127.0.0.1:<port>`. Script tự tạo publisher và worker theo case, không cần dùng worker của dashboard.
