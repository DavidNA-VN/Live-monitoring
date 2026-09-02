const TYPE_LABELS = Object.freeze({
    BLACK_SCREEN: 'Black screen',
    REPEATED_BLACK_SCREEN: 'Repeated black screen',
    AUDIO_LOSS: 'Audio loss',
    VIDEO_FREEZE: 'Video freeze',
    REPEATED_VIDEO_FREEZE: 'Repeated video freeze',
    RUNTIME_HEALTH: 'Runtime health'
});

export function presentAlert(alert) {
    const state = String(alert?.state || 'OPEN').toUpperCase();
    const eventType = String(alert?.event_type || 'ALERT').toUpperCase();
    const reason = String(alert?.reason || 'alert_received');
    const severity = String(alert?.attributes?.severity || '').toUpperCase();
    const interrupted = state === 'RESOLVED' && reason === 'observation_gap';

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
        visualClass,
        lifecycleLabel,
        interrupted
    };
}
