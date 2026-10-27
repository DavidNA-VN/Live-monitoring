# Kiểm tra gói source

Ngày kiểm tra: 27/10/2026.

- Đã giải nén ZIP sang thư mục khác và chạy pytest với working directory tại bản giải nén.
- Lượt đầu khi Docker tắt: 842 passed, 64 skipped, không có test fail.
- Đã bật Docker/Redis và chạy lại toàn bộ suite trên bản giải nén, với `RUN_WORKER_MEDIA_E2E=1`, `RUN_PHASE6_LIVE=1`, `REQUIRE_MVP_E2E=1`: **906 passed, không có test skip hoặc fail**, thời gian 193,13 giây.
- `scripts/run_worker_e2e.py fast -q`: 3 passed.
- `scripts/run_mvp_e2e.py fast`: 139 test Python và 15 test JavaScript đều pass.
- Đã chạy `generate_monitoring_test_cases.py --reset` từ bản giải nén và tạo đủ bảy fixture.
- Đã chạy `verify_monitoring_test_cases.py` với HTTP origin dùng `HlsRequestHandler` của release trên cổng riêng, publisher tốc độ 2x và worker riêng theo từng case: **7/7 pass**.
- Đối chiếu Redis độc lập theo số lượng chính xác: healthy 0, black_screen 4, video_freeze 4, audio_silence 2, audio_missing 1, macroblocking 2, combined 8 alert nội dung.
- Mỗi case có đúng một variant và không dropped media segment. Các alert ID không trùng, payload qua được parser API, có variant và URL segment đầu/cuối. Không có storage_id trong public attributes.
- Báo cáo sample nằm trong `DEMO_VERIFICATION.json`. URL localhost trong báo cáo là URL của HTTP server tạm khi kiểm thử, không phải URL cần dùng để demo sau này.
- 15 test JavaScript của dashboard đều pass trên Node.js 22.23.2.
- Entry point `src/live_main.py --help` chạy được trong bản giải nén.
- Kiểm tra các import trực tiếp tới script trong gói không phát hiện dependency thiếu.
- Đã kiểm tra tính toàn vẹn ZIP và SHA-256 của file trong manifest.
- Python dùng để kiểm thử: 3.13.9, với môi trường thư viện có sẵn trên máy phát triển. Chưa kiểm chứng cài dependency mới trên máy khác.

Gói lấy bản demo đã commit làm nền, bổ sung bản sửa lưu trạng thái freeze sau phục hồi và các điều chỉnh kiểm thử media tương ứng. Không lấy những thay đổi nghiên cứu macroblocking offline chưa commit vào gói.

Macroblocking live dạng lưới và các lớp tích hợp liên quan được giữ vì thuộc dependency của ứng dụng demo. Phạm vi demo chính của gói là black-screen, freeze và audio-loss.

Chạy lại các bước trong `DEMO_GUIDE.md` khi đã bật Redis và cài FFmpeg trước khi báo cáo. Commit nền và các bản sửa đã chọn nằm trong `RELEASE_INFO.json`. Manifest SHA-256 được giữ trong gói ZIP gửi mentor, không dùng để xác minh bản checkout Git có thể thay đổi kiểu xuống dòng. Lượt xác minh với Docker không yêu cầu sửa code production trong gói; đã bổ sung ghi chú về HTTP origin cho lệnh verifier.

## Bản công bố trên GitHub

Source trong `src/`, `tests/`, `scripts/`, `contracts/` và file dependency/cấu hình kiểm thử được đối chiếu với bản gửi mentor. Bản GitHub bổ sung README và hai ảnh chụp hệ thống. Nhánh nghiên cứu offline tại workspace gốc không được đưa vào bản công bố này.
