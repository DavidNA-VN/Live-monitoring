/**
 * LifecycleController: Máy trạng thái điều phối vòng đời của Stream trên giao diện.
 * Tuân thủ quy chuẩn:
 * - Không coi 202 ACCEPTED là đã chạy thành công.
 * - Chờ command result APPLIED/NOOP và status RUNNING mới bật media + alerts.
 * - STOP dọn dẹp sạch sẽ tài nguyên, hủy polling và abort pending requests.
 */
export class LifecycleController {
    constructor({ apiClient, view, mediaSession, alertClient }) {
        this.apiClient = apiClient;
        this.view = view;
        this.mediaSession = mediaSession;
        this.alertClient = alertClient;

        this.state = 'IDLE';
        this.currentStreamId = null;
        this.currentMasterUrl = null;
        this.statusPollInterval = null;
        this.statusPollInFlight = false;
        this.activeAbortController = null;

        this.setState('IDLE');
    }

    setState(newState) {
        this.state = newState;
        this.view.setButtonsState(newState);
    }

    async _pollCommandResult(commandId, signal, maxWaitMs = 30000) {
        const startTime = Date.now();
        while (Date.now() - startTime < maxWaitMs) {
            if (signal && signal.aborted) return null;

            const res = await this.apiClient.getCommand(commandId, signal);
            if (res.ok && res.isFinal && res.data) {
                return res.data;
            }

            // Sleep 500ms
            await new Promise(resolve => setTimeout(resolve, 500));
        }
        return null; // Timeout
    }

    async _pollStatusUntil(streamId, targetStatus, maxWaitMs = 15000, signal) {
        const startTime = Date.now();
        while (Date.now() - startTime < maxWaitMs) {
            if (signal && signal.aborted) return null;

            const res = await this.apiClient.getStatus(streamId, signal);
            if (res.ok && res.data && res.data.status === targetStatus) {
                return res.data;
            }

            await new Promise(resolve => setTimeout(resolve, 500));
        }
        return null; // Timeout
    }

    async _pollStatusRemoved(streamId, maxWaitMs = 15000, signal) {
        const startTime = Date.now();
        while (Date.now() - startTime < maxWaitMs) {
            if (signal && signal.aborted) return false;
            const res = await this.apiClient.getStatus(streamId, signal);
            if (res.notFound) return true;
            await new Promise(resolve => setTimeout(resolve, 500));
        }
        return false;
    }

    async handleStart(streamId, masterUrl) {
        if (!streamId || !masterUrl) return;

        // Dọn dẹp session cũ nếu có
        await this.disposeCurrentSession();

        this.activeAbortController = new AbortController();
        const signal = this.activeAbortController.signal;

        this.currentStreamId = streamId;
        this.currentMasterUrl = masterUrl;

        this.setState('SUBMITTING_START');
        this.view.addSystemLog(`Đang gửi lệnh Connect tới luồng ${streamId}...`);

        const startRes = await this.apiClient.startStream(streamId, masterUrl, null, signal);
        if (!startRes.ok) {
            this.setState('FAILED');
            const errDetail = startRes.data?.detail || startRes.error || 'Lỗi không xác định từ Backend';
            this.view.addSystemLog(`Lỗi Connect: ${errDetail}`, 'error');
            this.view.setSystemStatus('FAILED', 'var(--danger)');
            return;
        }

        const commandId = startRes.data?.command_id;
        if (!commandId) {
            this.setState('FAILED');
            this.view.addSystemLog("Backend không trả về command_id", 'error');
            return;
        }

        this.setState('WAITING_START_RESULT');
        this.view.addSystemLog(`Đã tiếp nhận lệnh (ID: ${commandId.slice(0, 8)}...). Đang xử lý...`);

        const cmdResult = await this._pollCommandResult(commandId, signal, 30000);
        if (signal.aborted) return;

        if (!cmdResult) {
            this.setState('FAILED');
            this.view.addSystemLog("Quá thời gian chờ kết quả xử lý lệnh START từ Worker", 'error');
            this.view.setSystemStatus('TIMEOUT', 'var(--danger)');
            return;
        }

        if (cmdResult.status === 'REJECTED' || cmdResult.status === 'FAILED') {
            this.setState('FAILED');
            const reason = cmdResult.error || cmdResult.error_code || 'Lệnh bị từ chối';
            this.view.addSystemLog(`Lệnh START thất bại: ${reason}`, 'error');
            this.view.setSystemStatus(cmdResult.status, 'var(--danger)');
            return;
        }

        this.setState('STARTING');
        this.view.addSystemLog(`Lệnh START đã được áp dụng (${cmdResult.status}). Đang đồng bộ trạng thái luồng...`);

        const statusData = await this._pollStatusUntil(streamId, 'RUNNING', 15000, signal);
        if (signal.aborted) return;

        if (!statusData) {
            this.setState('FAILED');
            this.view.addSystemLog("Quá thời gian chờ luồng chuyển sang trạng thái RUNNING", 'warning');
            this.view.setSystemStatus('STARTING_TIMEOUT', 'var(--warning)');
            return;
        }

        // Đã xác nhận RUNNING hoàn toàn
        this.setState('RUNNING');
        this.view.setSystemStatus('RUNNING', 'var(--success)');
        this.view.addSystemLog(`Luồng ${streamId} đang hoạt động. Khởi chạy Player & Alert Stream...`);

        this.mediaSession.start(masterUrl);
        this.alertClient.start(streamId);
        this._startStatusPolling(streamId);
    }

    async handlePause() {
        if (this.state !== 'RUNNING' || !this.currentStreamId) return;

        this.setState('SUBMITTING_PAUSE');
        this.view.addSystemLog("Đang gửi lệnh Pause...");

        const signal = this.activeAbortController ? this.activeAbortController.signal : null;
        const res = await this.apiClient.pauseStream(this.currentStreamId, null, signal);
        if (!res.ok) {
            this.setState('RUNNING');
            this.view.addSystemLog(`Lỗi Pause: ${res.data?.detail || res.error}`, 'error');
            return;
        }

        const cmdResult = await this._pollCommandResult(res.data.command_id, signal);
        if (cmdResult && (cmdResult.status === 'APPLIED' || cmdResult.status === 'NOOP')) {
            const paused = await this._pollStatusUntil(this.currentStreamId, 'PAUSED', 10000, signal);
            if (!paused) {
                this.setState('RUNNING');
                this.view.addSystemLog('Worker accepted PAUSE but runtime did not reach PAUSED.', 'warning');
                return;
            }
            this.setState('PAUSED');
            this.view.setSystemStatus('PAUSED', 'var(--warning)');
            this.mediaSession.pause();
            this.view.addSystemLog("Luồng đã tạm dừng (PAUSED).");
        } else {
            this.setState('RUNNING');
            this.view.addSystemLog(`Không thể tạm dừng luồng: ${cmdResult?.error || 'Unknown error'}`, 'error');
        }
    }

    async handleResume() {
        if (this.state !== 'PAUSED' || !this.currentStreamId) return;

        this.setState('SUBMITTING_RESUME');
        this.view.addSystemLog("Đang gửi lệnh Resume...");

        const signal = this.activeAbortController ? this.activeAbortController.signal : null;
        const res = await this.apiClient.resumeStream(this.currentStreamId, null, signal);
        if (!res.ok) {
            this.setState('PAUSED');
            this.view.addSystemLog(`Lỗi Resume: ${res.data?.detail || res.error}`, 'error');
            return;
        }

        const cmdResult = await this._pollCommandResult(res.data.command_id, signal);
        if (cmdResult && (cmdResult.status === 'APPLIED' || cmdResult.status === 'NOOP')) {
            const running = await this._pollStatusUntil(this.currentStreamId, 'RUNNING', 10000, signal);
            if (!running) {
                this.setState('PAUSED');
                this.view.addSystemLog('Worker accepted RESUME but runtime did not reach RUNNING.', 'warning');
                return;
            }
            this.setState('RUNNING');
            this.view.setSystemStatus('RUNNING', 'var(--success)');
            this.mediaSession.resume();
            this.view.addSystemLog("Luồng đã tiếp tục phát (RUNNING).");
        } else {
            this.setState('PAUSED');
            this.view.addSystemLog(`Không thể tiếp tục luồng: ${cmdResult?.error || 'Unknown error'}`, 'error');
        }
    }

    async handleStop() {
        if (this.state === 'IDLE') return;

        const streamId = this.currentStreamId;
        const previousState = this.state;
        this.setState('SUBMITTING_STOP');
        this.view.addSystemLog("Đang gửi lệnh Stop...");

        if (streamId) {
            const signal = this.activeAbortController ? this.activeAbortController.signal : null;
            const stopRes = await this.apiClient.stopStream(streamId, null, signal);
            const commandId = stopRes.data?.command_id;
            if (!stopRes.ok || !commandId) {
                this.setState(previousState === 'FAILED' ? 'FAILED' : previousState);
                this.view.addSystemLog(`Không thể gửi STOP: ${stopRes.data?.detail || stopRes.error || 'Backend error'}`, 'error');
                return;
            }

            const result = await this._pollCommandResult(commandId, signal);
            if (!result || (result.status !== 'APPLIED' && result.status !== 'NOOP')) {
                this.setState(previousState === 'FAILED' ? 'FAILED' : previousState);
                this.view.addSystemLog(`STOP không được áp dụng: ${result?.error || result?.error_code || 'Timeout'}`, 'error');
                return;
            }

            const removed = await this._pollStatusRemoved(streamId, 15000, signal);
            if (!removed) {
                this.setState('FAILED');
                this.view.addSystemLog('STOP đã được áp dụng nhưng runtime status chưa được gỡ bỏ.', 'warning');
                return;
            }
        }

        await this.disposeCurrentSession();
        this.setState('IDLE');
        this.view.resetTelemetry();
        this.view.addSystemLog("Luồng đã dừng hoàn toàn.");
    }

    _startStatusPolling(streamId) {
        if (this.statusPollInterval) clearInterval(this.statusPollInterval);

        this.statusPollInterval = setInterval(async () => {
            if (this.state !== 'RUNNING' && this.state !== 'PAUSED') return;
            if (this.currentStreamId !== streamId) return;
            if (this.statusPollInFlight) return;

            this.statusPollInFlight = true;
            try {
                const res = await this.apiClient.getStatus(streamId);
                if (this.currentStreamId !== streamId) return;
                if (res.ok && res.data) {
                    const status = res.data.status;
                    if (status === 'RUNNING' && this.state === 'RUNNING') {
                        this.view.setSystemStatus('RUNNING', 'var(--success)');
                    } else if (status === 'PAUSED' && this.state === 'PAUSED') {
                        this.view.setSystemStatus('PAUSED', 'var(--warning)');
                    } else if (status === 'FAILED') {
                        this.setState('FAILED');
                        this.view.setSystemStatus('FAILED', 'var(--danger)');
                        this.view.addSystemLog(`Trạng thái luồng chuyển sang FAILED: ${res.data.error || 'Worker error'}`, 'error');
                        this._disposeRuntimeResources();
                    }
                } else if (res.notFound && (this.state === 'RUNNING' || this.state === 'PAUSED')) {
                    this.setState('IDLE');
                    await this.disposeCurrentSession();
                    this.view.resetTelemetry();
                    this.view.addSystemLog("Luồng đã bị gỡ bỏ khỏi hệ thống.", 'warning');
                }
            } finally {
                this.statusPollInFlight = false;
            }
        }, 3000);
    }

    async disposeCurrentSession() {
        this._disposeRuntimeResources();

        if (this.activeAbortController) {
            this.activeAbortController.abort();
            this.activeAbortController = null;
        }

        this.currentStreamId = null;
        this.currentMasterUrl = null;
    }

    _disposeRuntimeResources() {
        if (this.statusPollInterval) {
            clearInterval(this.statusPollInterval);
            this.statusPollInterval = null;
        }
        this.statusPollInFlight = false;

        this.alertClient.stop();
        this.mediaSession.dispose();
    }
}
