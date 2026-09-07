import assert from 'node:assert/strict';
import test from 'node:test';

import {
    presentAlert
} from '../../src/presentation/static/js/alert-presentation.js';


test('freeze warning is rendered as warning severity', () => {
    const result = presentAlert({
        event_type: 'VIDEO_FREEZE',
        state: 'OPEN',
        reason: 'freeze_warning_threshold',
        attributes: { severity: 'WARNING' }
    });

    assert.equal(result.visualClass, 'warning');
    assert.equal(result.lifecycleLabel, 'OPEN');
    assert.equal(result.severity, 'WARNING');
    assert.equal(result.typeLabel, 'Video freeze');
});

test('alert presentation exposes a readable rendition label', () => {
    const named = presentAlert({
        event_type: 'BLACK_SCREEN',
        state: 'OPEN',
        variant_id: '360p',
        variant_stable_id: '1234567890abcdef'
    });
    const stableOnly = presentAlert({
        event_type: 'BLACK_SCREEN',
        state: 'OPEN',
        variant_stable_id: '1234567890abcdef'
    });

    assert.equal(named.variantLabel, '360p');
    assert.equal(stableOnly.variantLabel, '12345678');
});


test('freeze update is rendered as alert on the same lifecycle', () => {
    const result = presentAlert({
        event_type: 'VIDEO_FREEZE',
        state: 'UPDATE',
        reason: 'freeze_alert_threshold',
        attributes: { severity: 'ALERT' }
    });

    assert.equal(result.visualClass, 'error');
    assert.equal(result.lifecycleLabel, 'UPDATE');
    assert.equal(result.severity, 'ALERT');
});


test('video_returned is recovered but observation_gap is interrupted', () => {
    const recovered = presentAlert({
        event_type: 'VIDEO_FREEZE',
        state: 'RESOLVED',
        reason: 'video_returned',
        attributes: { severity: 'ALERT' }
    });
    const interrupted = presentAlert({
        event_type: 'VIDEO_FREEZE',
        state: 'RESOLVED',
        reason: 'observation_gap',
        attributes: { severity: 'WARNING' }
    });

    assert.equal(recovered.visualClass, 'resolved');
    assert.equal(recovered.lifecycleLabel, 'RESOLVED');
    assert.equal(interrupted.visualClass, 'warning');
    assert.equal(interrupted.lifecycleLabel, 'INTERRUPTED');
    assert.equal(interrupted.interrupted, true);
});


test('repeated freeze uses the shared alert presentation contract', () => {
    const result = presentAlert({
        event_type: 'REPEATED_VIDEO_FREEZE',
        state: 'OPEN',
        reason: 'repeated_video_freeze',
        attributes: { severity: 'ALERT' }
    });

    assert.equal(result.visualClass, 'error');
    assert.equal(result.typeLabel, 'Repeated video freeze');
});
