import json
import shutil
import subprocess

import pytest

from scripts.benchmark_macroblocking import load_manifest
from scripts.generate_macroblocking_fixtures import generate_fixtures


@pytest.mark.integration
def test_generated_macroblocking_hls_and_manifest_are_consumable(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg/FFprobe is unavailable")
    output = tmp_path / "macroblocking"
    generate_fixtures(output)
    manifest = load_manifest(output / "manifest.json")
    assert len(manifest["fixtures"]) == 5

    for fixture in manifest["fixtures"]:
        playlist = output / fixture["playlist"]
        assert playlist.is_file()
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(playlist),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        stream = json.loads(completed.stdout)["streams"][0]
        assert (stream["width"], stream["height"]) == (640, 360)
