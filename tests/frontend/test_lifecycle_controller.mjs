import assert from 'node:assert/strict';
import test from 'node:test';

import { LifecycleController } from '../../src/presentation/static/js/lifecycle-controller.js';

function makeController(apiOverrides = {}) {
    const calls = { mediaPause: 0, mediaDispose: 0, alertStop: 0, resets: 0 };
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
        resetTelemetry() { calls.resets += 1; },
    };
    const mediaSession = {
        pause() { calls.mediaPause += 1; },
        resume() {},
        dispose() { calls.mediaDispose += 1; },
    };
    const alertClient = { stop() { calls.alertStop += 1; } };
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
