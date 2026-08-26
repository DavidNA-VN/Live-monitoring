import inspect
import json
from pathlib import Path

from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.runtime_status_reader import RuntimeStatusReader
from core.stream_supervisor import StreamSupervisor
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
)

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def test_runtime_status_enums_match_json_schema():
    with (CONTRACT_ROOT / "runtime-status.schema.json").open(encoding="utf-8") as f:
        schema = json.load(f)

    props = schema["properties"]

    schema_statuses = set(props["status"]["enum"])
    enum_statuses = {status.value for status in PublicStreamStatus}
    assert enum_statuses == schema_statuses

    schema_healths = set(props["health"]["enum"])
    enum_healths = {health.value for health in RuntimeHealth}
    assert enum_healths == schema_healths

    schema_check_values = set(props["checks"]["additionalProperties"]["enum"])
    enum_check_values = {status.value for status in CheckStatus}
    assert enum_check_values == schema_check_values


def test_supervisor_runtime_status_reader_implements_protocol():
    class DummyFactory:
        def create(self, _config):
            raise NotImplementedError

    supervisor = StreamSupervisor(session_factory=DummyFactory())
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=None)

    assert isinstance(reader, RuntimeStatusReader)


def test_runtime_status_modules_contain_no_detector_or_media_imports():
    import app.supervisor_runtime_status as app_mod
    import core.runtime_status_reader as core_mod
    import models.runtime_status as model_mod

    for mod in (model_mod, core_mod, app_mod):
        source = inspect.getsource(mod)
        assert "checks.black_screen" not in source
        assert "checks.audio_loss" not in source
        assert "profiles" not in source
        assert "ffmpeg" not in source
