import { ApiClient } from './api-client.js?v=6.0';
import { DashboardView } from './dashboard-view.js?v=6.0';
import { MediaSession } from './media-session.js?v=6.0';
import { AlertClient } from './alert-client.js?v=6.0';
import { LifecycleController } from './lifecycle-controller.js?v=6.0';

/**
 * App Entrypoint: Khởi tạo các module và gắn kết giao diện điều khiển.
 */
document.addEventListener('DOMContentLoaded', async () => {
    const apiClient = new ApiClient();
    const view = new DashboardView();

    // 1. Kiểm tra readiness và server mode lúc khởi động
    let serverMode = 'unknown';
    try {
        const readyRes = await apiClient.getHealthReady();
        if (readyRes.ok && readyRes.data && readyRes.data.mode) {
            serverMode = readyRes.data.mode;
        }
    } catch (e) {
        console.warn("Unable to probe /health/ready:", e);
    }
    view.setModeBadge(serverMode);

    // 2. Khởi tạo MediaSession & AlertClient
    const mediaSession = new MediaSession({
        videoEl: view.videoEl,
        audioLevelBar: view.audioLevelBar,
        mode: serverMode,
        onTelemetry: (telemetry) => view.setTelemetry(telemetry),
        onError: (err) => view.addSystemLog(`Lỗi Media Player: ${err.details || err.type}`, 'error')
    });

    const alertClient = new AlertClient({
        apiClient,
        onAlert: (alert) => view.addAlertLog(alert),
        onStatusChange: (status) => view.setConnectionStatus(status)
    });

    // 3. Khởi tạo LifecycleController
    const controller = new LifecycleController({
        apiClient,
        view,
        mediaSession,
        alertClient
    });

    // 4. Gắn kết UI Events
    if (view.streamForm) {
        view.streamForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const streamId = view.streamIdInput.value.trim();
            const masterUrl = view.masterUrlInput.value.trim();
            if (streamId && masterUrl) {
                controller.handleStart(streamId, masterUrl, {
                    videoFreezeEnabled: (
                        view.freezeEnabledInput?.checked === true
                    )
                });
            }
        });
    }

    if (view.pauseBtn) {
        view.pauseBtn.addEventListener('click', () => controller.handlePause());
    }

    if (view.resumeBtn) {
        view.resumeBtn.addEventListener('click', () => controller.handleResume());
    }

    if (view.stopBtn) {
        view.stopBtn.addEventListener('click', () => controller.handleStop());
    }

    window.addEventListener('pagehide', () => {
        alertClient.stop();
        mediaSession.destroy();
    }, { once: true });
});
