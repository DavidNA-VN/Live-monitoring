from fastapi.testclient import TestClient
import pytest

from presentation.api.main import create_app
from presentation.api.adapters.fakes import FakeAlertSource, FakeMonitoringControl


def test_api_rest_control_endpoints():
    app = create_app(enable_fake_generator=False)
    with TestClient(app) as client:
        # 1. Start stream
        start_payload = {
            "schema_version": "1.0",
            "stream_id": "chan-01",
            "master_url": "https://example.com/master.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -35.0,
                    "duration_seconds": 4.0,
                    "track_index": 0,
                },
            },
        }

        # Start mismatch ID
        res_mismatch = client.post("/api/v1/streams/chan-02/start", json=start_payload)
        assert res_mismatch.status_code == 400

        # Start success
        res_start = client.post("/api/v1/streams/chan-01/start", json=start_payload)
        assert res_start.status_code == 202
        start_data = res_start.json()
        assert start_data["schema_version"] == "1.0"
        assert start_data["command_id"]
        assert start_data["status"] == "ACCEPTED"
        start_cmd_id = start_data["command_id"]

        # Check command result
        res_cmd = client.get(f"/api/v1/commands/{start_cmd_id}")
        assert res_cmd.status_code == 200
        cmd_data = res_cmd.json()
        assert cmd_data["command_type"] == "START"
        assert cmd_data["status"] == "APPLIED"

        # Check status
        res_status = client.get("/api/v1/streams/chan-01/status")
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["status"] == "RUNNING"
        assert status_data["worker_id"] == "fake-worker-01"

        # 2. Pause
        res_pause = client.post("/api/v1/streams/chan-01/pause")
        assert res_pause.status_code == 202
        pause_data = res_pause.json()
        assert pause_data["status"] == "ACCEPTED"

        res_status = client.get("/api/v1/streams/chan-01/status")
        assert res_status.status_code == 200
        assert res_status.json()["status"] == "PAUSED"

        # 3. Resume
        res_resume = client.post("/api/v1/streams/chan-01/resume")
        assert res_resume.status_code == 202
        res_status = client.get("/api/v1/streams/chan-01/status")
        assert res_status.status_code == 200
        assert res_status.json()["status"] == "RUNNING"

        # 4. Stop
        res_stop = client.post("/api/v1/streams/chan-01/stop")
        assert res_stop.status_code == 202
        res_status = client.get("/api/v1/streams/chan-01/status")
        assert res_status.status_code == 404

        # 5. Non-existent command
        res_bad_cmd = client.get("/api/v1/commands/unknown-command-id")
        assert res_bad_cmd.status_code == 404


def test_api_events_and_websocket():
    alert_source = FakeAlertSource()
    app = create_app(alert_source=alert_source, enable_fake_generator=False)

    with TestClient(app) as client:
        # Trigger fake alert generation with fast interval
        alert_source.start_generating_fake_alerts(
            "chan-ws-01",
            interval_seconds=0.01,
            recovery_seconds=0.01,
        )

        with client.websocket_connect("/api/v1/ws/streams/chan-ws-01") as websocket:
            msg = websocket.receive_json()
            assert msg["message_type"] == "ALERT"
            assert msg["stream_id"] == "chan-ws-01"
            payload = msg["payload"]
            assert payload["schema_version"] == "1.0"
            assert payload["stream_id"] == "chan-ws-01"
            assert payload["event_type"] == "BLACK_SCREEN"
            assert payload["state"] == "OPEN"

        # Test events REST endpoint
        res_events = client.get("/api/v1/streams/chan-ws-01/events")
        assert res_events.status_code == 200
        events = res_events.json()
        assert len(events) >= 1
        assert events[0]["stream_id"] == "chan-ws-01"


def test_dashboard_home_endpoint():
    app = create_app(enable_fake_generator=False)
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")


def test_unversioned_prototype_routes_are_not_public():
    app = create_app(enable_fake_generator=False)
    with TestClient(app) as client:
        assert client.get("/streams/unknown/status").status_code == 404


def test_custom_control_requires_explicit_read_ports():
    class CustomControl(FakeMonitoringControl):
        pass

    # A real control adapter must not silently receive unrelated fake readers.
    control = CustomControl()
    assert create_app(control=control, enable_fake_generator=False) is not None

    from presentation.api.adapters.base import MonitoringControl

    class WriteOnlyControl(MonitoringControl):
        async def start_stream(self, config, *, idempotency_key=None):
            raise NotImplementedError

        async def pause_stream(self, stream_id, *, idempotency_key=None):
            raise NotImplementedError

        async def resume_stream(self, stream_id, *, idempotency_key=None):
            raise NotImplementedError

        async def stop_stream(self, stream_id, *, idempotency_key=None):
            raise NotImplementedError

        async def update_config(self, config, *, idempotency_key=None):
            raise NotImplementedError

    with pytest.raises(ValueError, match="requires command_results"):
        create_app(control=WriteOnlyControl(), enable_fake_generator=False)


def test_api_command_pending_lookup():
    from presentation.api.adapters.base import CommandLookup, CommandLookupState, CommandResultReader
    from presentation.api.models import CommandSubmissionDTO

    class FakePendingReader(CommandResultReader):
        async def get_command_result(self, command_id: str) -> CommandLookup:
            if command_id == "pending-123":
                return CommandLookup(
                    state=CommandLookupState.PENDING,
                    submission=CommandSubmissionDTO(
                        command_id="pending-123",
                        stream_id="chan-01",
                        status="ACCEPTED",
                    ),
                )
            return CommandLookup(state=CommandLookupState.MISSING)

    app = create_app(
        command_results=FakePendingReader(),
        enable_fake_generator=False,
    )
    with TestClient(app) as client:
        res = client.get("/api/v1/commands/pending-123")
        assert res.status_code == 202
        data = res.json()
        assert data["command_id"] == "pending-123"
        assert data["status"] == "ACCEPTED"


def test_api_error_mappings():
    from presentation.api.adapters.base import MonitoringControl
    from presentation.api.adapters.redis_monitoring_control import (
        ControlRedisUnavailableError,
        IdempotencyConflictError,
    )

    class FailingControl(MonitoringControl):
        async def start_stream(self, config, *, idempotency_key=None):
            if idempotency_key == "conflict":
                raise IdempotencyConflictError("Key conflict")
            raise ControlRedisUnavailableError("Redis down")

        async def pause_stream(self, stream_id, *, idempotency_key=None):
            pass
        async def resume_stream(self, stream_id, *, idempotency_key=None):
            pass
        async def stop_stream(self, stream_id, *, idempotency_key=None):
            pass
        async def update_config(self, config, *, idempotency_key=None):
            pass

    app = create_app(
        control=FailingControl(),
        command_results=FakeMonitoringControl(),
        status_reader=FakeMonitoringControl(),
        enable_fake_generator=False,
    )
    with TestClient(app) as client:
        payload = {
            "schema_version": "1.0",
            "stream_id": "chan-01",
            "master_url": "https://example.com/master.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {"enabled": False, "threshold_dbfs": -30.0, "duration_seconds": 3.0},
            },
        }
        res_conflict = client.post(
            "/api/v1/streams/chan-01/start",
            json=payload,
            headers={"Idempotency-Key": "conflict"},
        )
        assert res_conflict.status_code == 409

        res_503 = client.post(
            "/api/v1/streams/chan-01/start",
            json=payload,
            headers={"Idempotency-Key": "other"},
        )
        assert res_503.status_code == 503
