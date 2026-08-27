import os
from uuid import uuid4
import pytest

from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import RedisNamespace
from tests.e2e.backend_probe import BackendProbe


@pytest.fixture
def redis_context():
    client = RedisClient(
        RedisSettings(
            url=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"),
            socket_connect_timeout=1.0,
            socket_timeout=3.0,
        )
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Disposable Redis is unavailable for E2E tests: {exc}")

    namespace = RedisNamespace(f"media-monitor:e2e:{uuid4().hex}")
    try:
        yield client, namespace
    finally:
        try:
            found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
            if found:
                client.client.delete(*found)
        except Exception:
            pass
        finally:
            client.close()


@pytest.fixture
def probe(redis_context):
    client, namespace = redis_context
    return BackendProbe(redis_client=client, namespace=namespace)
