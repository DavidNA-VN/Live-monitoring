import assert from 'node:assert/strict';
import test from 'node:test';

import { LifecycleController } from '../../src/presentation/static/js/lifecycle-controller.js';
import { AlertClient } from '../../src/presentation/static/js/alert-client.js';

function makeController(apiOverrides = {}) {
    const calls = { mediaPause: 0, mediaDispose: 0, alertStart: 0, alertStop: 0, resets: 0 };
    const apiClient = {
        pauseStream: async () => ({ ok: true, data: { command_id: 'pause-1' } }),
        resumeStream: async () => ({ ok: true, data: { command_id: 'resume-1' } }),
        stopStream: async () => ({ ok: true, data: { command_id: 'stop-1' } }),
        ...apiOverrides,
    };
    const view = {
        setButtonsState() {},
        addSystemLog() {},
        setSystemStatus() {},
        setRuntimeTelemetry() {},
        resetTelemetry() { calls.resets += 1; },
    };
    const mediaSession = {
        pause() { calls.mediaPause += 1; },
        resume() {},
        dispose() { calls.mediaDispose += 1; },
    };
    const alertClient = {
        start() { calls.alertStart += 1; },
        stop() { calls.alertStop += 1; },
    };
    const controller = new LifecycleController({ apiClient, view, mediaSession, alertClient });
    controller.currentStreamId = 'channel-01';
    controller.currentMasterUrl = 'https://example.test/master.m3u8';
    controller.activeAbortController = new AbortController();
    return { controller, calls };
}

test('pause does not claim PAUSED before runtime confirmation', async () => {
    const { controller, calls } = makeController();
    controller.state = 'RUNNING';
    controller._pollCommandResult = async () => ({ status: 'APPLIED' });
    controller._pollStatusUntil = async () => null;

    await controller.handlePause();

    assert.equal(controller.state, 'RUNNING');
    assert.equal(calls.mediaPause, 0);
});

test('START forwards freeze and variant selection configuration', async () => {
    let receivedOptions = null;
    const { controller } = makeController({
        startStream: async (
            _streamId,
            _masterUrl,
            _idempotencyKey,
            _signal,
            options
        ) => {
            receivedOptions = options;
            return { ok: false, status: 503 };
        },
    });

    await controller.handleStart(
        'channel-01',
        'https://example.test/master.m3u8',
        {
            videoFreezeEnabled: true,
            variantSelectionMode: 'representative',
            representativeVariantCount: 3
        }
    );

    assert.deepEqual(receivedOptions, {
        videoFreezeEnabled: true,
        variantSelectionMode: 'representative',
        representativeVariantCount: 3
    });
});

test('failed STOP submission preserves the active session', async () => {
    const { controller, calls } = makeController({
        stopStream: async () => ({ ok: false, status: 503, data: { detail: 'Unavailable' } }),
    });
    controller.state = 'RUNNING';

    await controller.handleStop();

    assert.equal(controller.state, 'RUNNING');
    assert.equal(controller.currentStreamId, 'channel-01');
    assert.equal(calls.mediaDispose, 0);
    assert.equal(calls.alertStop, 0);
});

test('STOP cleans local resources only after command and status confirmation', async () => {
    const { controller, calls } = makeController();
    controller.state = 'RUNNING';
    controller._pollCommandResult = async () => ({ status: 'APPLIED' });
    controller._pollStatusRemoved = async () => true;

    await controller.handleStop();

    assert.equal(controller.state, 'IDLE');
    assert.equal(controller.currentStreamId, null);
    assert.equal(calls.mediaDispose, 1);
    assert.equal(calls.alertStop, 1);
    assert.equal(calls.resets, 1);
});

test('media startup failure does not block alert subscription', async () => {
    const { controller, calls } = makeController({
        startStream: async () => ({ ok: true, data: { command_id: 'start-1' } }),
    });
    controller.mediaSession.start = () => { throw new Error('HLS unavailable'); };
    controller._pollCommandResult = async () => ({ status: 'APPLIED' });
    controller._pollStatusUntil = async () => ({ status: 'RUNNING' });
    controller._startStatusPolling = () => {};

    await controller.handleStart('channel-01', 'https://example.test/master.m3u8');

    assert.equal(controller.state, 'RUNNING');
    assert.equal(calls.alertStart, 1);
});

test('alert history loads even before WebSocket connects', async () => {
    const alerts = [];
    const client = new AlertClient({
        apiClient: {
            getRecentAlerts: async () => ({
                ok: true,
                data: [{ alert_id: 'alert-1', event_type: 'BLACK_SCREEN', state: 'OPEN' }],
            }),
        },
        onAlert: alert => alerts.push(alert),
    });
    client._connect = () => {};

    client.start('channel-01');
    await new Promise(resolve => setTimeout(resolve, 0));

    assert.equal(alerts.length, 1);
    assert.equal(alerts[0].alert_id, 'alert-1');
});

test('alert client keeps freeze lifecycle messages and deduplicates replays', () => {
    const received = [];
    const client = new AlertClient({
        apiClient: {},
        onAlert: alert => received.push(alert),
    });
    const lifecycle = [
        { alert_id: 'freeze-open', event_id: 'freeze-1', state: 'OPEN' },
        { alert_id: 'freeze-update', event_id: 'freeze-1', state: 'UPDATE' },
        {
            alert_id: 'freeze-resolved',
            event_id: 'freeze-1',
            state: 'RESOLVED'
        },
    ];

    lifecycle.forEach(alert => client._processAlert(alert));
    lifecycle.forEach(alert => client._processAlert(alert));

    assert.deepEqual(
        received.map(alert => alert.state),
        ['OPEN', 'UPDATE', 'RESOLVED']
    );
    assert.equal(new Set(received.map(alert => alert.event_id)).size, 1);
});
