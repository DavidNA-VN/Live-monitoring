import pytest

from presentation.api.settings import ApiSettings


def test_settings_default_fake_mode():
    settings = ApiSettings()
    assert settings.mode == "fake"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.redis_prefix == "media-monitor:v1"
    assert settings.alert_history_scan_limit == 1000
    assert settings.websocket_redis_block_ms == 1000
    assert settings.enable_fake_generator is False


def test_settings_rejects_invalid_mode():
    with pytest.raises(ValueError, match="Invalid MONITORING_API_MODE 'prod'"):
        ApiSettings(mode="prod")  # type: ignore

    with pytest.raises(ValueError, match="Invalid MONITORING_API_MODE 'true'"):
        ApiSettings(mode="true")  # type: ignore


def test_settings_rejects_non_positive_limits():
    with pytest.raises(ValueError, match="ALERT_HISTORY_SCAN_LIMIT must be a positive integer"):
        ApiSettings(alert_history_scan_limit=-5)

    with pytest.raises(ValueError, match="WEBSOCKET_REDIS_BLOCK_MS must be a positive integer"):
        ApiSettings(websocket_redis_block_ms=0)


def test_settings_rejects_empty_prefix_or_redis_url():
    with pytest.raises(ValueError, match="REDIS_PREFIX must not be empty"):
        ApiSettings(redis_prefix="   ")

    with pytest.raises(ValueError, match="REDIS_URL must not be empty when mode is 'redis'"):
        ApiSettings(mode="redis", redis_url="   ")


def test_settings_from_env_parsing():
    env = {
        "MONITORING_API_MODE": "redis",
        "REDIS_URL": "redis://custom-host:6380/2",
        "REDIS_PREFIX": "custom-prefix",
        "ALERT_HISTORY_SCAN_LIMIT": "200",
        "WEBSOCKET_REDIS_BLOCK_MS": "50",
        "ENABLE_FAKE_GENERATOR": "false",
    }
    settings = ApiSettings.from_env(env)
    assert settings.mode == "redis"
    assert settings.redis_url == "redis://custom-host:6380/2"
    assert settings.redis_prefix == "custom-prefix"
    assert settings.alert_history_scan_limit == 200
    assert settings.websocket_redis_block_ms == 50
    assert settings.enable_fake_generator is False


def test_settings_from_env_rejects_invalid_boolean():
    env = {"ENABLE_FAKE_GENERATOR": "sometimes"}
    with pytest.raises(ValueError, match="ENABLE_FAKE_GENERATOR must be one of"):
        ApiSettings.from_env(env)
