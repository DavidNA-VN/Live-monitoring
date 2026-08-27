# PHASE 4: UI DASHBOARD COMPLETION

Tài liệu này tổng hợp các thay đổi và hoàn thiện cho phần Giao diện Giám sát (Live Monitoring Dashboard).

## 1. Cải tiến Thiết kế (UI/UX)
- Chuyển đổi giao diện sang phong cách tối (Dark Mode) kết hợp hiệu ứng kính mờ (Glassmorphism) hiện đại, chuyên nghiệp.
- Căn chỉnh bố cục: Video và Telemetry nằm ở trung tâm; Event Log được cấp phát toàn bộ khu vực cột bên phải để dễ dàng theo dõi dòng thời gian.
- Xóa bỏ các biểu tượng cảnh báo lỗi đè lên Video Player gây cản trở tầm nhìn. Video vẫn tiếp tục chạy liên tục và hiển thị đúng thực trạng của luồng gốc.
- Thay đổi Font chữ sang `Outfit` (cho các tiêu đề, số liệu nổi bật) và `Inter` (cho văn bản thông thường).
- Cải tiến Nút bấm (Connect, Pause, Resume, Stop) với dải màu Gradient (linear-gradient) và hiệu ứng đổ bóng.

## 2. Giao diện Giám sát Kỹ thuật (Telemetry)
- **Thiết kế mới**: Gộp các thông số (System Status, Resolution, FPS, Bitrate) vào một thanh ngang (pill-shaped) mượt mà, phân cách bằng các vạch kẻ tinh tế.
- **Dữ liệu Mock**: Bổ sung hàm giả lập dữ liệu cho FPS (giao động 59-60) và Bitrate để kiểm thử tính năng hiển thị trực tiếp trên giao diện khi video bắt đầu chạy.
- Trạng thái `System Status` luôn duy trì màu xanh ngọc (`RUNNING`), không bị đổi thành `ERROR` khi có cảnh báo nhằm giữ cho giao diện nhất quán.

## 3. Hệ thống Cảnh báo (Event Log)
- Logic bắt lỗi `BLACK_SCREEN` và `AUDIO_LOSS` đã được điều chỉnh để đọc đúng trường `event_type`.
- Xóa bỏ các log có trạng thái `RESOLVED` (Đã giải quyết) để tránh làm loãng màn hình. Hệ thống chỉ ghi nhận các log Cảnh báo Rủi ro (`OPEN`).
- Tinh chỉnh CSS cho các Cảnh báo Mất hình/Mất tiếng: Loại bỏ viền kẻ dọc thô cứng, thay bằng nền đỏ mờ (`rgba(239, 68, 68, 0.15)`) nổi bật nhưng dễ chịu.

## 4. Tình trạng Tích hợp
- Tách rời giao diện với Backend thật bằng mẫu Adapter (`FakeMonitoringControl`, `FakeAlertSource`).
- Sẵn sàng tiến tới Phase 5: Tích hợp Backend thật (Redis) và Cơ sở dữ liệu (PostgreSQL) mà không cần chỉnh sửa giao diện.
