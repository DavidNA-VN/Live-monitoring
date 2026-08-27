from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any, Callable, TypeVar
from uuid import uuid4

from core.redis_client import RedisClient
from core.redis_keys import (
    AlertRedisKeys,
    ControlRedisKeys,
    DesiredStateRedisKeys,
    PublicRuntimeRedisKeys,
    RedisNamespace,
    WorkerRedisKeys,
)
from models.alert import AlertEnvelope

T = TypeVar("T")


def sample_stream_config_dict(stream_id: str, master_url: str = "https://example.test/live.m3u8") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "stream_id": stream_id,
        "master_url": master_url,
        "checks": {
            "black_screen": {"enabled": True},
            "audio_loss": {
                "enabled": True,
                "threshold_dbfs": -60.0,
                "duration_seconds": 30.0,
                "track_index": 0,
            },
        },
    }


class BackendProbe:
    """Simulates a backend service interacting with monitoring workers strictly via public Redis interfaces."""

    def __init__(self, *, redis_client: RedisClient, namespace: RedisNamespace) -> None:
        self.client = redis_client.client if hasattr(redis_client, "client") else redis_client
        self.namespace = namespace
        self.control_keys = ControlRedisKeys(namespace)
        self.public_keys = PublicRuntimeRedisKeys(namespace)
        self.worker_keys = WorkerRedisKeys(namespace)
        self.desired_keys = DesiredStateRedisKeys(namespace)
        self.alert_keys = AlertRedisKeys(namespace)

    def wait_until(
        self,
        predicate: Callable[[], T | None],
        timeout: float = 5.0,
        interval: float = 0.05,
        description: str = "condition",
    ) -> T:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            res = predicate()
            if res:
                return res
            time.sleep(interval)
        res = predicate()
        if res:
            return res
        raise AssertionError(f"Timed out after {timeout}s waiting for {description}")

    def send_command(
        self,
        action: str,
        stream_id: str,
        config: dict[str, Any] | None = None,
        command_id: str | None = None,
        requested_at: datetime | None = None,
    ) -> str:
        cid = command_id or f"cmd-{uuid4().hex[:12]}"
        if config is None and action in ("START", "UPDATE_CONFIG"):
            config = sample_stream_config_dict(stream_id)

        payload = json.dumps({
            "schema_version": "1.0",
            "command_id": cid,
            "command_type": action,
            "stream_id": stream_id,
            "requested_at": (requested_at or datetime.now(timezone.utc)).isoformat(),
            "config": config,
        })
        self.client.xadd(self.control_keys.commands(), {"payload": payload})
        return cid

    def wait_command_delivery_settled(self, timeout: float = 5.0) -> bool:
        """Wait until the worker group has delivered and acknowledged every command."""

        def _check() -> bool | None:
            try:
                groups = self.client.xinfo_groups(self.control_keys.commands())
            except Exception:
                return None
            for group in groups:
                name = group.get("name") or group.get(b"name")
                if isinstance(name, bytes):
                    name = name.decode("utf-8")
                if name != "monitoring-workers":
                    continue
                pending = group.get("pending", group.get(b"pending", 0))
                lag = group.get("lag", group.get(b"lag", 0))
                return True if int(pending or 0) == 0 and int(lag or 0) == 0 else None
            return None

        return self.wait_until(
            _check,
            timeout=timeout,
            description="monitoring command stream to become idle",
        )

    def command_result_count(self, command_id: str) -> int:
        count = 0
        for _, fields in self.client.xrange(self.control_keys.command_results()):
            raw_payload = fields.get("payload") or fields.get(b"payload")
            if raw_payload is None:
                continue
            text = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else str(raw_payload)
            try:
                if json.loads(text).get("command_id") == command_id:
                    count += 1
            except json.JSONDecodeError:
                continue
        return count

    def wait_command_result(self, command_id: str, timeout: float = 5.0) -> dict[str, Any]:
        def _check() -> dict[str, Any] | None:
            entries = self.client.xrange(self.control_keys.command_results())
            for _, fields in entries:
                raw_payload = fields.get("payload") or fields.get(b"payload")
                if raw_payload:
                    text = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else str(raw_payload)
                    try:
                        data = json.loads(text)
                        if data.get("command_id") == command_id:
                            return data
                    except json.JSONDecodeError:
                        pass
            return None

        return self.wait_until(_check, timeout=timeout, description=f"command result for command_id={command_id}")

    def wait_runtime_status(
        self,
        stream_id: str,
        expected_status: str | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        def _check() -> dict[str, Any] | None:
            raw = self.client.hget(self.public_keys.current_statuses(), stream_id)
            if raw is None:
                return None
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
            if expected_status is not None and data.get("status") != expected_status:
                return None
            if predicate is not None and not predicate(data):
                return None
            return data

        desc = f"runtime status for stream_id={stream_id}"
        if expected_status:
            desc += f" with status={expected_status}"
        return self.wait_until(_check, timeout=timeout, description=desc)

    def wait_runtime_status_removed(self, stream_id: str, timeout: float = 5.0) -> bool:
        def _check() -> bool | None:
            raw = self.client.hget(self.public_keys.current_statuses(), stream_id)
            return True if raw is None else None

        return self.wait_until(_check, timeout=timeout, description=f"runtime status removal for stream_id={stream_id}")

    def wait_status_update(
        self,
        stream_id: str,
        update_type: str | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        def _check() -> dict[str, Any] | None:
            entries = self.client.xrange(self.public_keys.status_updates())
            for _, fields in entries:
                raw_payload = fields.get("payload") or fields.get(b"payload")
                if raw_payload:
                    text = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else str(raw_payload)
                    try:
                        data = json.loads(text)
                        if data.get("stream_id") == stream_id:
                            if update_type is None or data.get("update_type") == update_type:
                                return data
                    except json.JSONDecodeError:
                        pass
            return None

        desc = f"status update for stream_id={stream_id}"
        if update_type:
            desc += f" of type={update_type}"
        return self.wait_until(_check, timeout=timeout, description=desc)

    def wait_worker_state(
        self,
        worker_id: str,
        expected_state: str | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        def _check() -> dict[str, Any] | None:
            raw = self.client.get(self.worker_keys.heartbeat(worker_id))
            if raw is None:
                return None
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
            if expected_state is not None and data.get("state") != expected_state:
                return None
            if predicate is not None and not predicate(data):
                return None
            return data

        desc = f"worker heartbeat for worker_id={worker_id}"
        if expected_state:
            desc += f" with state={expected_state}"
        return self.wait_until(_check, timeout=timeout, description=desc)

    def get_desired_state(self, stream_id: str) -> dict[str, Any] | None:
        raw = self.client.hget(self.desired_keys.current_states(), stream_id)
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return json.loads(text)

    def read_alerts(self, stream_id: str | None = None) -> list[AlertEnvelope]:
        entries = self.client.xrange(self.alert_keys.outbox())
        envelopes: list[AlertEnvelope] = []
        for _, fields in entries:
            try:
                env = AlertEnvelope.from_redis_fields(fields)
                if stream_id is None or env.stream_id == stream_id:
                    envelopes.append(env)
            except Exception:
                pass
        return envelopes

    def wait_alert(
        self,
        predicate: Callable[[AlertEnvelope], bool],
        stream_id: str | None = None,
        timeout: float = 10.0,
    ) -> AlertEnvelope:
        def _check() -> AlertEnvelope | None:
            alerts = self.read_alerts(stream_id)
            for alert in alerts:
                if predicate(alert):
                    return alert
            return None

        return self.wait_until(_check, timeout=timeout, description="matching alert in outbox")
