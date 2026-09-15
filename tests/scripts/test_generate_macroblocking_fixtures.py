import json

import pytest

from scripts.generate_macroblocking_fixtures import (
    FIXTURES,
    OWNERSHIP_MARKER,
    build_ffmpeg_command,
    build_filter_graph,
    generate_fixtures,
    manifest_dict,
)


def test_positive_fixtures_have_area_bands_and_hard_negative_exists():
    manifest = manifest_dict()
    positives = [
        item
        for fixture in manifest["fixtures"]
        for item in fixture["ranges"]
        if item["label"] == "macroblocking"
    ]
    assert positives
    assert all(len(item["expected_area_ratio"]) == 2 for item in positives)
    assert any(
        fixture["artifact_kind"] == "natural_grid_negative"
        for fixture in manifest["fixtures"]
    )


def test_local_filter_uses_crop_and_nearest_neighbor_reconstruction():
    spec = next(item for item in FIXTURES if item.name == "local_small")
    graph = build_filter_graph(spec)
    assert graph is not None
    assert "crop=256:144:32:72" in graph
    assert "flags=neighbor" in graph
    assert "noise=alls=32" in graph
    assert "between(t,2,14)" in graph


def test_command_produces_single_high_variant_hls(tmp_path):
    spec = next(item for item in FIXTURES if item.name == "local_large")
    command = build_ffmpeg_command("ffmpeg", spec, tmp_path)
    assert command.count("-i") == 1
    assert "-filter_complex" in command
    assert str(tmp_path / spec.name / "high" / "segment_%05d.ts") in command


def test_reset_refuses_directory_without_ownership_marker(tmp_path):
    output = tmp_path / "foreign"
    output.mkdir()
    with pytest.raises(ValueError, match="not owned"):
        generate_fixtures(output, reset=True)


def test_manifest_is_ascii_json_serializable():
    encoded = json.dumps(manifest_dict(), sort_keys=True).encode("ascii")
    assert encoded
    assert OWNERSHIP_MARKER.startswith(".")
