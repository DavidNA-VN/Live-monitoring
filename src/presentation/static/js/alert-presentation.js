const TYPE_LABELS = Object.freeze({
    BLACK_SCREEN: 'Black screen',
    REPEATED_BLACK_SCREEN: 'Repeated black screen',
    AUDIO_LOSS: 'Audio loss',
    VIDEO_FREEZE: 'Video freeze',
    REPEATED_VIDEO_FREEZE: 'Repeated video freeze',
    MACROBLOCKING: 'Macroblocking',
    RUNTIME_HEALTH: 'Runtime health'
});

export function presentAlert(alert) {
    const state = String(alert?.state || 'OPEN').toUpperCase();
    const eventType = String(alert?.event_type || 'ALERT').toUpperCase();
    const reason = String(alert?.reason || 'alert_received');
    const severity = String(alert?.attributes?.severity || '').toUpperCase();
    const variantId = String(alert?.variant_id || '').trim();
    const variantStableId = String(alert?.variant_stable_id || '').trim();
    const interrupted = state === 'RESOLVED' && reason === 'observation_gap';
    const attributes = alert?.attributes || {};
    const startSegmentUri = String(attributes.start_segment_uri || '').trim();
    const endSegmentUri = String(attributes.end_segment_uri || '').trim();
    const startSequence = String(attributes.start_sequence || '').trim();
    const endSequence = String(attributes.end_sequence || '').trim();
    const affectedSegmentCount = String(
        attributes.affected_segment_count || ''
    ).trim();
    const averageArea = Number(attributes.average_affected_area_ratio);
    const peakArea = Number(attributes.peak_affected_area_ratio);
    const areaSummary = eventType === 'MACROBLOCKING'
        && Number.isFinite(averageArea)
        && Number.isFinite(peakArea)
        ? `Affected area: avg ${(averageArea * 100).toFixed(1)}%, peak ${(peakArea * 100).toFixed(1)}%`
        : null;

    let visualClass = 'error';
    let lifecycleLabel = state;
    if (interrupted) {
        visualClass = 'warning';
        lifecycleLabel = 'INTERRUPTED';
    } else if (state === 'RESOLVED' || state === 'RECOVERED') {
        visualClass = 'resolved';
    } else if (state === 'DEGRADED' || severity === 'WARNING') {
        visualClass = 'warning';
    }

    return {
        eventType,
        typeLabel: TYPE_LABELS[eventType] || eventType,
        reason,
        severity: severity === 'WARNING' || severity === 'ALERT' ? severity : null,
        variantLabel: variantId || (
            variantStableId ? variantStableId.slice(0, 8) : null
        ),
        visualClass,
        lifecycleLabel,
        interrupted,
        areaSummary,
        segmentRange: startSegmentUri ? {
            startUri: startSegmentUri,
            endUri: endSegmentUri || startSegmentUri,
            startSequence,
            endSequence: endSequence || startSequence,
            affectedSegmentCount,
            coverageComplete: attributes.coverage_complete !== 'false'
        } : null
    };
}

export function safeSegmentUrl(value) {
    try {
        const parsed = new URL(value);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch {
        return null;
    }
}
