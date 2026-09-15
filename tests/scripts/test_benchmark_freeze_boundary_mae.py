import base64
import json

import pytest

from scripts.benchmark_freeze_boundary_mae import (
    LabeledBoundaryPair,
    evaluate_pairs,
    load_pairs,
)


def test_evaluate_pairs_builds_confusion_matrix():
    same = LabeledBoundaryPair("same", True, bytes([100]) * 1024, bytes([101]) * 1024)
    different = LabeledBoundaryPair("different", False, bytes([20]) * 1024, bytes([200]) * 1024)
    result = evaluate_pairs((same, different), (0.01,))[0]
    assert result == {
        "threshold": 0.01, "true_positive": 1, "true_negative": 1,
        "false_positive": 0, "false_negative": 0, "accuracy": 1.0,
    }


def test_load_pairs_validates_sample_size_and_base64(tmp_path):
    path = tmp_path / "pairs.json"
    encoded = base64.b64encode(bytes(1024)).decode("ascii")
    path.write_text(json.dumps([{
        "name": "same", "expected_match": True,
        "left_pixels_base64": encoded, "right_pixels_base64": encoded,
    }]), encoding="utf-8")
    assert load_pairs(path)[0].normalized_mae == 0

    path.write_text(json.dumps([{
        "name": "bad", "expected_match": True,
        "left_pixels_base64": "!!!", "right_pixels_base64": encoded,
    }]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pairs(path)
