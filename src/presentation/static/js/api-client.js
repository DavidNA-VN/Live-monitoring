/**
 * ApiClient: Quản lý toàn bộ REST HTTP calls tới Presentation Backend.
 * Tuân thủ quy chuẩn: Idempotency-Key, HTTP status differentiation, error handling.
 */
export class ApiClient {
    constructor(baseUrl = `${window.location.origin}/api/v1`) {
        this.baseUrl = baseUrl;
    }

    generateIdempotencyKey() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    async _safeJson(res) {
        try {
            return await res.json();
        } catch {
            return null;
        }
    }

    async getHealthReady(signal = null) {
        try {
            const res = await fetch(`${window.location.origin}/health/ready`, { signal });
            const data = await this._safeJson(res);
            return { ok: res.ok, status: res.status, data };
        } catch (err) {
            return { ok: false, status: 0, error: err.message };
        }
    }

    async startStream(
        streamId,
        masterUrl,
        idempotencyKey = null,
        signal = null,
        options = {}
    ) {
        const key = idempotencyKey || this.generateIdempotencyKey();
        const payload = {
            schema_version: "1.0",
            stream_id: streamId,
            master_url: masterUrl,
            checks: {
                black_screen: { enabled: true },
                audio_loss: {
                    enabled: true,
                    threshold_dbfs: -60.0,
                    duration_seconds: 30.0,
                    track_index: 0
                },
                video_freeze: {
                    enabled: options.videoFreezeEnabled === true,
                    noise_db: -60.0,
                    detector_minimum_duration: 0.2,
                    warning_duration_seconds: 3.0,
                    alert_duration_seconds: 5.0
                }
            }
        };

        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/start`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Idempotency-Key': key
                },
                body: JSON.stringify(payload),
                signal
            });
            const data = await this._safeJson(res);
            return { ok: res.status === 202, status: res.status, data, key };
        } catch (err) {
            return { ok: false, status: 0, error: err.message, key };
        }
    }

    async pauseStream(streamId, idempotencyKey = null, signal = null) {
        const key = idempotencyKey || this.generateIdempotencyKey();
        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/pause`, {
                method: 'POST',
                headers: { 'Idempotency-Key': key },
                signal
            });
            const data = await this._safeJson(res);
            return { ok: res.status === 202, status: res.status, data, key };
        } catch (err) {
            return { ok: false, status: 0, error: err.message, key };
        }
    }

    async resumeStream(streamId, idempotencyKey = null, signal = null) {
        const key = idempotencyKey || this.generateIdempotencyKey();
        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/resume`, {
                method: 'POST',
                headers: { 'Idempotency-Key': key },
                signal
            });
            const data = await this._safeJson(res);
            return { ok: res.status === 202, status: res.status, data, key };
        } catch (err) {
            return { ok: false, status: 0, error: err.message, key };
        }
    }

    async stopStream(streamId, idempotencyKey = null, signal = null) {
        const key = idempotencyKey || this.generateIdempotencyKey();
        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/stop`, {
                method: 'POST',
                headers: { 'Idempotency-Key': key },
                signal
            });
            const data = await this._safeJson(res);
            return { ok: res.status === 202, status: res.status, data, key };
        } catch (err) {
            return { ok: false, status: 0, error: err.message, key };
        }
    }

    async getCommand(commandId, signal = null) {
        try {
            const res = await fetch(`${this.baseUrl}/commands/${encodeURIComponent(commandId)}`, { signal });
            const data = await this._safeJson(res);
            return {
                ok: res.ok,
                status: res.status,
                isPending: res.status === 202,
                isFinal: res.status === 200,
                data
            };
        } catch (err) {
            return { ok: false, status: 0, error: err.message };
        }
    }

    async getStatus(streamId, signal = null) {
        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/status`, { signal });
            const data = await this._safeJson(res);
            return {
                ok: res.ok,
                status: res.status,
                notFound: res.status === 404,
                data
            };
        } catch (err) {
            return { ok: false, status: 0, error: err.message };
        }
    }

    async getRecentAlerts(streamId, limit = 50, signal = null) {
        try {
            const res = await fetch(`${this.baseUrl}/streams/${encodeURIComponent(streamId)}/events?limit=${limit}`, { signal });
            const data = await this._safeJson(res);
            return {
                ok: res.ok,
                status: res.status,
                data: Array.isArray(data) ? data : []
            };
        } catch (err) {
            return { ok: false, status: 0, data: [], error: err.message };
        }
    }
}
