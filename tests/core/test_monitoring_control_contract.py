import inspect
import json
from pathlib import Path

from app.supervisor_monitoring_control import SupervisorMonitoringControl
from core.monitoring_control import (
    MonitoringAction,
    MonitoringControl,
    MonitoringControlResult,
)
from core.stream_supervisor import StreamSupervisor

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def test_monitoring_action_matches_json_schema():
    with (CONTRACT_ROOT / "monitoring-command.schema.json").open(encoding="utf-8") as f:
        schema = json.load(f)

    schema_actions = set(schema["properties"]["command_type"]["enum"])
    enum_actions = {action.value for action in MonitoringAction}

    assert enum_actions == schema_actions


def test_monitoring_control_result_exposes_no_storage_identity():
    result = MonitoringControlResult(
        stream_id="channel-01",
        action=MonitoringAction.START,
        changed=True,
    )

    assert result.stream_id == "channel-01"
    assert result.action is MonitoringAction.START
    assert result.changed is True
    assert not hasattr(result, "storage_id")


def test_supervisor_monitoring_control_implements_protocol():
    class DummyFactory:
        def create(self, _config):
            raise NotImplementedError

    supervisor = StreamSupervisor(session_factory=DummyFactory())
    adapter = SupervisorMonitoringControl(supervisor)

    assert isinstance(adapter, MonitoringControl)


def test_monitoring_control_modules_have_no_detector_or_media_imports():
    import app.supervisor_monitoring_control as adapter_mod
    import core.monitoring_control as control_mod

    for mod in (control_mod, adapter_mod):
        source = inspect.getsource(mod)
        assert "checks" not in source
        assert "profiles" not in source
        assert "ffmpeg" not in source
        assert "storage_id" not in source
