import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiClient } from '../../src/presentation/static/js/api-client.js';


test('START payload carries explicit freeze configuration', async () => {
    let request = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        request = { url, options };
        return {
            status: 202,
            ok: true,
            async json() {
                return { command_id: 'command-1', status: 'ACCEPTED' };
            }
        };
    };

    try {
        const client = new ApiClient('http://localhost/api/v1');
        const result = await client.startStream(
            'channel-01',
            'https://example.test/master.m3u8',
            'idempotency-1',
            null,
            { videoFreezeEnabled: true }
        );
        const payload = JSON.parse(request.options.body);

        assert.equal(result.ok, true);
        assert.equal(payload.checks.video_freeze.enabled, true);
        assert.equal(payload.checks.video_freeze.noise_db, -60.0);
        assert.equal(payload.checks.video_freeze.warning_duration_seconds, 3.0);
        assert.equal(payload.checks.video_freeze.alert_duration_seconds, 5.0);
    } finally {
        globalThis.fetch = originalFetch;
    }
});
