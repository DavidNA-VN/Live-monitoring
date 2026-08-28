from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import pytest
import redis.exceptions

from presentation.api.composition import PresentationDependencies
from presentation.api.main import create_app
from presentation.api.settings import ApiSettings


def test_health_live_fake_mode():
    app = create_app(settings=ApiSettings(mode="fake"))
    with TestClient(app) as client:
        res = client.get("/health/live")
        assert res.status_code == 200
        assert res.json() == {"status": "alive"}


def test_health_ready_fake_mode():
    app = create_app(settings=ApiSettings(mode="fake"))
    with TestClient(app) as client:
        res = client.get("/health/ready")
        assert res.status_code == 200
        assert res.json() == {"status": "ready", "mode": "fake"}


def test_health_live_redis_mode():
    mock_redis = AsyncMock()
    mock_control = AsyncMock()
    mock_alert = AsyncMock()

    deps = PresentationDependencies(
        control=mock_control,
        command_results=mock_control,
        status_reader=mock_control,
        alert_source=mock_alert,
        redis_client=mock_redis,
        mode="redis",
    )
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        res = client.get("/health/live")
        assert res.status_code == 200
        assert res.json() == {"status": "alive"}


def test_injected_dependencies_do_not_read_process_environment(monkeypatch):
    monkeypatch.setenv("MONITORING_API_MODE", "invalid")
    mock_control = AsyncMock()
    mock_alert = AsyncMock()
    deps = PresentationDependencies(
        control=mock_control,
        command_results=mock_control,
        status_reader=mock_control,
        alert_source=mock_alert,
        mode="fake",
    )

    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200


def test_health_ready_redis_mode_success():
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    mock_control = AsyncMock()
    mock_alert = AsyncMock()

    deps = PresentationDependencies(
        control=mock_control,
        command_results=mock_control,
        status_reader=mock_control,
        alert_source=mock_alert,
        redis_client=mock_redis,
        mode="redis",
    )
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        res = client.get("/health/ready")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert data["mode"] == "redis"
        assert data["dependencies"] == {"redis": "available"}


def test_health_ready_redis_mode_failure_returns_sanitized_503():
    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = redis.exceptions.ConnectionError("Connection refused to redis://secret:pass@private-host:6379")
    mock_control = AsyncMock()
    mock_alert = AsyncMock()

    deps = PresentationDependencies(
        control=mock_control,
        command_results=mock_control,
        status_reader=mock_control,
        alert_source=mock_alert,
        redis_client=mock_redis,
        mode="redis",
    )
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        res = client.get("/health/ready")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "not_ready"
        assert data["mode"] == "redis"
        assert data["dependencies"] == {"redis": "unavailable"}
        # Ensure password or URL is not leaked
        assert "secret" not in res.text
        assert "pass" not in res.text
        assert "private-host" not in res.text
