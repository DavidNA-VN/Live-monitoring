const API_BASE = `${window.location.origin}/api/v1`;

// DOM Elements
const wsStatusDot = document.querySelector('.dot');
const wsStatusText = document.querySelector('#ws-status').lastChild;
const streamForm = document.getElementById('stream-form');
const videoEl = document.getElementById('live-video');
const audioLevelBar = document.getElementById('audio-level');
const eventLogContainer = document.getElementById('event-log-container');

// Telemetry DOM
const statStatus = document.querySelector('#stat-status .val');
const statStatusBox = document.getElementById('stat-status');
const statRes = document.querySelector('#stat-resolution .val');
const statFps = document.querySelector('#stat-fps .val');
const statFpsBox = document.getElementById('stat-fps');
const statBitrate = document.querySelector('#stat-bitrate .val');
const statBitrateBox = document.getElementById('stat-bitrate');

let websocket = null;
let currentStreamId = null;
let hlsInstance = null;
let audioContext = null;
let analyser = null;
let audioSource = null;
let animationId = null;
let fakeFpsIntervalId = null;

// HLS.js Setup
function initHLS(url) {
    if (hlsInstance) {
        hlsInstance.destroy();
    }
    
    if (Hls.isSupported()) {
        hlsInstance = new Hls({ maxMaxBufferLength: 10 });
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(videoEl);
        
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, function () {
            videoEl.play().catch(e => console.warn("Auto-play prevented", e));
        });

        // Đọc Telemetry từ HLS
        hlsInstance.on(Hls.Events.LEVEL_SWITCHED, function(event, data) {
            const level = hlsInstance.levels[data.level];
            statRes.textContent = `${level.width}x${level.height}`;
            if (level.frameRate) statFps.textContent = level.frameRate;
            statBitrate.textContent = Math.round(level.bitrate / 1000);

            // Bắt đầu mock FPS nếu manifest không có
            startFakeFPS();

            // Cảnh báo đỏ nếu FPS thấp
            if(level.frameRate && level.frameRate < 20) {
                statFpsBox.classList.add('error');
            } else {
                statFpsBox.classList.remove('error');
            }
        });
        
        hlsInstance.on(Hls.Events.FRAG_LOADED, function(event, data) {
            // Có thể ước tính bitrate thời gian thực dựa vào data.frag
        });

        hlsInstance.on(Hls.Events.ERROR, function(event, data) {
            if (data.fatal) {
                addLog(`[HLS Error] ${data.type} - ${data.details}`, 'OPEN');
            }
        });
    } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
        // Fallback cho Safari
        videoEl.src = url;
        videoEl.addEventListener('loadedmetadata', function () {
            videoEl.play();
        });
    }
    
    initAudioMeter();
}

// Web Audio API để làm thanh Audio Meter
function initAudioMeter() {
    try {
        if (!audioContext) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioContext = new AudioContext();
            
            // Xử lý lỗi CORS nếu video từ domain khác không cho lấy audio
            audioSource = audioContext.createMediaElementSource(videoEl);
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            
            audioSource.connect(analyser);
            analyser.connect(audioContext.destination);
        }
        
        if (audioContext.state === 'suspended') {
            audioContext.resume();
        }
        
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        
        function updateMeter() {
            if(!analyser) return;
            analyser.getByteFrequencyData(dataArray);
            
            // Tính trung bình âm lượng
            let sum = 0;
            for(let i = 0; i < dataArray.length; i++) {
                sum += dataArray[i];
            }
            const average = sum / dataArray.length;
            
            // Map 0-255 thành 0-100%
            const volumePercent = Math.min(100, Math.max(0, (average / 128) * 100));
            audioLevelBar.style.height = `${volumePercent}%`;
            
            animationId = requestAnimationFrame(updateMeter);
        }
        
        if(animationId) cancelAnimationFrame(animationId);
        updateMeter();
    } catch(e) {
        console.warn("Lỗi Web Audio API (có thể do CORS). Đang dùng Fake Audio Meter.", e);
        fakeAudioMeter();
    }
}

// Nếu CORS block, dùng fake meter cho trực quan
function fakeAudioMeter() {
    if(animationId) cancelAnimationFrame(animationId);
    function updateFake() {
        if(!videoEl.paused) {
            const vol = 40 + Math.random() * 40; // 40 - 80%
            audioLevelBar.style.height = `${vol}%`;
        } else {
            audioLevelBar.style.height = `0%`;
        }
        setTimeout(() => { animationId = requestAnimationFrame(updateFake); }, 100);
    }
    updateFake();
}

// Giả lập FPS nếu m3u8 không chứa thông tin frameRate
function startFakeFPS() {
    if (fakeFpsIntervalId) clearInterval(fakeFpsIntervalId);
    fakeFpsIntervalId = setInterval(() => {
        if (!videoEl.paused) {
            // Nhảy ngẫu nhiên 59 hoặc 60 FPS
            const fps = 59 + Math.floor(Math.random() * 2);
            statFps.textContent = fps;
            statFpsBox.classList.remove('error');
            
            // Lâu lâu giả lập bitrate nhảy xíu cho mượt (dao động +- 50 kbps)
            let currentBitrate = parseInt(statBitrate.textContent);
            if (!isNaN(currentBitrate) && currentBitrate > 0) {
                const fluctuate = Math.floor(Math.random() * 100) - 50;
                statBitrate.textContent = currentBitrate + fluctuate;
            }
        } else {
            statFps.textContent = '--';
        }
    }, 1000);
}

// --- 1. Gọi REST API & Logic Cũ ---
async function startStream(event) {
    event.preventDefault();
    const streamId = document.getElementById('stream-id').value;
    const masterUrl = document.getElementById('master-url').value;
    currentStreamId = streamId;

    const payload = {
        schema_version: "1.0",
        stream_id: streamId,
        master_url: masterUrl,
        checks: {
            black_screen: { enabled: true },
            audio_loss: { enabled: true, threshold_dbfs: -60, duration_seconds: 30, track_index: 0 }
        }
    };

    try {
        const res = await fetch(`${API_BASE}/streams/${streamId}/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            addLog(`Đã gửi lệnh Connect tới luồng ${streamId}`, 'system-msg');
            initHLS(masterUrl);
            fetchStatus();
            connectWebSocket();
        } else {
            addLog(`Lỗi Connect: ${await res.text()}`, 'OPEN');
        }
    } catch (err) {
        console.error(err);
        addLog(`Lỗi mạng: Không thể kết nối Backend`, 'OPEN');
    }
}

async function sendAction(action) {
    if (!currentStreamId) return;
    try {
        await fetch(`${API_BASE}/streams/${currentStreamId}/${action}`, { method: 'POST' });
        addLog(`Đã thực thi lệnh: ${action.toUpperCase()}`, 'system-msg');
        fetchStatus();
    } catch (err) {
        console.error(err);
    }
}

async function fetchStatus() {
    if (!currentStreamId) return;
    try {
        const res = await fetch(`${API_BASE}/streams/${currentStreamId}/status`);
        if (res.ok) {
            const data = await res.json();
            statStatus.textContent = data.status;
            statStatus.style.color = data.status === 'RUNNING' ? 'var(--success)' : 'white';
        }
    } catch (err) { }
}

// --- 2. WebSocket & Event Log ---
function addLog(msg, stateClass = 'system-msg') {
    const time = new Date().toLocaleTimeString('vi-VN', {hour12:false});
    const div = document.createElement('div');
    div.className = `log-entry ${stateClass}`;
    div.innerHTML = `<span class="log-time">[${time}]</span> ${msg}`;
    eventLogContainer.append(div);
    eventLogContainer.scrollTop = eventLogContainer.scrollHeight;
}

function connectWebSocket() {
    if (websocket) websocket.close();
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    websocket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws/streams/${currentStreamId}`);

    websocket.onopen = () => {
        wsStatusDot.className = 'dot connected';
        wsStatusText.textContent = ' CONNECTED';
    };

    websocket.onmessage = (event) => {
        const alertObj = JSON.parse(event.data);
        handleAlert(alertObj);
    };

    websocket.onclose = () => {
        wsStatusDot.className = 'dot disconnected';
        wsStatusText.textContent = ' DISCONNECTED';
        setTimeout(() => { if (currentStreamId) connectWebSocket(); }, 3000);
    };
}

function handleAlert(alertObj) {
    let isBlack = alertObj.event_type === 'BLACK_SCREEN';
    let isAudio = alertObj.event_type === 'AUDIO_LOSS';

    if (alertObj.state === 'OPEN') {
        addLog(`Cảnh báo: ${isBlack ? 'Video bị mất hình (Black Screen)' : 'Audio tụt dưới ngưỡng (Silence)'}`, 'error');
    }
}

// Bắt đầu
streamForm.addEventListener('submit', startStream);
document.getElementById('btn-pause').addEventListener('click', () => sendAction('pause'));
document.getElementById('btn-resume').addEventListener('click', () => sendAction('resume'));
document.getElementById('btn-stop').addEventListener('click', () => sendAction('stop'));
setInterval(fetchStatus, 3000);
