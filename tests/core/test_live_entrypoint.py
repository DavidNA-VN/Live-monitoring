import importlib


def test_live_entrypoint_imports_with_active_architecture():
    module = importlib.import_module("live_main")

    assert callable(module.main)


def test_live_entrypoint_exposes_audio_monitoring_options():
    module = importlib.import_module("live_main")

    args = module.parse_args(
        [
            "--url",
            "https://example.test/master.m3u8",
            "--disable-black-screen",
            "--silence-threshold-dbfs",
            "-55",
            "--audio-loss-duration",
            "45",
            "--audio-track-index",
            "2",
            "--max-media-processes",
            "3",
            "--max-service-media-processes",
            "6",
        ]
    )

    assert args.disable_black_screen is True
    assert args.disable_audio_loss is False
    assert args.silence_threshold_dbfs == -55.0
    assert args.audio_loss_duration == 45.0
    assert args.audio_track_index == 2
    assert args.max_media_processes == 3
    assert args.max_service_media_processes == 6
