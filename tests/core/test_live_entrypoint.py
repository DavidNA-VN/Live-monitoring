import importlib


def test_live_entrypoint_imports_with_active_architecture():
    module = importlib.import_module("live_main")

    assert callable(module.main)

