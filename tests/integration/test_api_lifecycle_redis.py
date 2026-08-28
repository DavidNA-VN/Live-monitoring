import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from core.redis_client import RedisClient, RedisSettings
from presentation.api.main import create_app
from presentation.api.settings import ApiSettings

pytestmark = pytest.mark.redis_integration


@pytest.fixture
def redis_test_url():
    url = os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15")
    sync_client = RedisClient(
        RedisSettings(
            url=url,
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        sync_client.ping()
    except Exception as exc:
        sync_client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    try:
        yield url
    finally:
        sync_client.close()


def test_redis_mode_lifecycle_and_readiness(redis_test_url):
    prefix = f"media-monitor:test:{uuid4().hex}"
    settings = ApiSettings(
        mode="redis",
        redis_url=redis_test_url,
        redis_prefix=prefix,
        alert_history_scan_limit=100,
        websocket_redis_block_ms=50,
    )

    app = create_app(settings=settings)

    with TestClient(app) as client:
        # Live endpoint
        res_live = client.get("/health/live")
        assert res_live.status_code == 200
        assert res_live.json() == {"status": "alive"}

        # Ready endpoint (real Redis ping)
        res_ready = client.get("/health/ready")
        assert res_ready.status_code == 200
        data = res_ready.json()
        assert data["status"] == "ready"
        assert data["mode"] == "redis"
        assert data["dependencies"] == {"redis": "available"}


def test_static_files_resolution_independent_of_cwd(monkeypatch, tmp_path):
    # Change working directory to a temporary folder to prove static files do not depend on cwd
    monkeypatch.chdir(tmp_path)

    app = create_app(settings=ApiSettings(mode="fake"))

    with TestClient(app) as client:
        # Check Dashboard HTML
        res_index = client.get("/")
        assert res_index.status_code == 200
        assert "text/html" in res_index.headers.get("content-type", "")
        assert "LIVE MONITORING" in res_index.text

        # Check Static CSS
        res_css = client.get("/static/css/style.css")
        assert res_css.status_code == 200

        # Check Static JS
        res_js = client.get("/static/js/app.js")
        assert res_js.status_code == 200
