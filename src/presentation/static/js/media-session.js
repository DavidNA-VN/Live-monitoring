/**
 * MediaSession: Quản lý vòng đời HLS.js, Web Audio API và Telemetry.
 * Đảm bảo dọn dẹp sạch sẽ khi dispose(), không rò rỉ tài nguyên,
 * và chỉ chạy fake telemetry khi server ở chế độ fake mode.
 */
export class MediaSession {
    constructor({ videoEl, audioLevelBar, mode = 'fake', onTelemetry = null, onError = null }) {
        this.videoEl = videoEl;
        this.audioLevelBar = audioLevelBar;
        this.mode = mode; // 'fake' | 'redis'
        this.onTelemetry = onTelemetry;
        this.onError = onError;

        this.hls = null;
        this.audioContext = null;
        this.analyser = null;
        this.audioSource = null;
        this.animationId = null;
        this.fakeTelemetryInterval = null;
        this.disposed = false;
    }

    start(masterUrl) {
        this.dispose();
        this.disposed = false;

        if (typeof Hls !== 'undefined' && Hls.isSupported()) {
            this.hls = new Hls({ maxMaxBufferLength: 10 });
            this.hls.loadSource(masterUrl);
            this.hls.attachMedia(this.videoEl);

            this.hls.on(Hls.Events.MANIFEST_PARSED, () => {
                if (this.disposed) return;
                this.videoEl.play().catch(e => console.warn("Autoplay prevented:", e));
            });

            this.hls.on(Hls.Events.LEVEL_SWITCHED, (event, data) => {
                if (this.disposed || !this.hls) return;
                const level = this.hls.levels[data.level];
                if (!level) return;

                const resolution = `${level.width}x${level.height}`;
                const bitrate = Math.round(level.bitrate / 1000);
                const fps = level.frameRate || (this.mode === 'fake' ? 60 : null);

                if (this.onTelemetry) {
                    this.onTelemetry({ resolution, bitrate, fps });
                }
            });

            this.hls.on(Hls.Events.ERROR, (event, data) => {
                if (this.disposed) return;
                if (data.fatal && this.onError) {
                    this.onError(data);
                }
            });
        } else if (this.videoEl.canPlayType('application/vnd.apple.mpegurl')) {
            this.videoEl.src = masterUrl;
            this.videoEl.addEventListener('loadedmetadata', () => {
                if (this.disposed) return;
                this.videoEl.play().catch(e => console.warn("Autoplay prevented:", e));
            }, { once: true });
        }

        this._initAudioMeter();

        // Chỉ bật fake telemetry khi đang ở fake mode
        if (this.mode === 'fake') {
            this._startFakeTelemetry();
        }
    }

    pause() {
        if (this.videoEl) {
            this.videoEl.pause();
        }
    }

    resume() {
        if (this.videoEl && !this.disposed) {
            this.videoEl.play().catch(e => console.warn("Resume play prevented:", e));
        }
    }

    _initAudioMeter() {
        try {
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!AudioCtx) return;

            if (!this.audioContext) {
                this.audioContext = new AudioCtx();
                this.audioSource = this.audioContext.createMediaElementSource(this.videoEl);
                this.analyser = this.audioContext.createAnalyser();
                this.analyser.fftSize = 256;

                this.audioSource.connect(this.analyser);
                this.analyser.connect(this.audioContext.destination);
            }
            if (this.audioContext.state === 'suspended') {
                this.audioContext.resume().catch(() => {});
            }

            const dataArray = new Uint8Array(this.analyser.frequencyBinCount);

            const updateMeter = () => {
                if (this.disposed || !this.analyser || !this.audioLevelBar) return;
                this.analyser.getByteFrequencyData(dataArray);

                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    sum += dataArray[i];
                }
                const average = sum / dataArray.length;
                const volumePercent = Math.min(100, Math.max(0, (average / 128) * 100));
                this.audioLevelBar.style.height = `${volumePercent}%`;

                this.animationId = requestAnimationFrame(updateMeter);
            };

            updateMeter();
        } catch (e) {
            // CORS restriction: Web Audio cannot inspect cross-origin video audio track without CORS headers
            if (this.mode === 'fake') {
                this._startFakeAudioMeter();
            } else if (this.audioLevelBar) {
                this.audioLevelBar.style.height = '0%';
            }
        }
    }

    _startFakeAudioMeter() {
        const updateFake = () => {
            if (this.disposed || !this.audioLevelBar) return;
            if (this.videoEl && !this.videoEl.paused) {
                const vol = 30 + Math.floor(Math.random() * 40);
                this.audioLevelBar.style.height = `${vol}%`;
            } else {
                this.audioLevelBar.style.height = '0%';
            }
            if (!this.disposed) {
                this.animationId = requestAnimationFrame(updateFake);
            }
        };
        updateFake();
    }

    _startFakeTelemetry() {
        if (this.fakeTelemetryInterval) clearInterval(this.fakeTelemetryInterval);
        this.fakeTelemetryInterval = setInterval(() => {
            if (this.disposed) return;
            if (this.videoEl && !this.videoEl.paused) {
                const fps = 59 + Math.floor(Math.random() * 2);
                if (this.onTelemetry) {
                    this.onTelemetry({ fps });
                }
            }
        }, 1000);
    }

    dispose() {
        this.disposed = true;

        if (this.fakeTelemetryInterval) {
            clearInterval(this.fakeTelemetryInterval);
            this.fakeTelemetryInterval = null;
        }

        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
            this.animationId = null;
        }

        if (this.hls) {
            try {
                this.hls.destroy();
            } catch {}
            this.hls = null;
        }

        if (this.videoEl) {
            try {
                this.videoEl.pause();
                this.videoEl.removeAttribute('src');
                this.videoEl.load();
            } catch {}
        }

        if (this.audioContext) {
            this.audioContext.suspend().catch(() => {});
        }

        if (this.audioLevelBar) {
            this.audioLevelBar.style.height = '0%';
        }
    }

    destroy() {
        this.dispose();
        if (this.audioSource) {
            try { this.audioSource.disconnect(); } catch {}
            this.audioSource = null;
        }
        if (this.analyser) {
            try { this.analyser.disconnect(); } catch {}
            this.analyser = null;
        }
        if (this.audioContext) {
            this.audioContext.close().catch(() => {});
            this.audioContext = null;
        }
    }
}
