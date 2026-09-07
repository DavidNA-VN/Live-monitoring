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


def test_live_entrypoint_exposes_resource_pool_tuning_options():
    module = importlib.import_module("live_main")

    args = module.parse_args(
        [
            "--command-worker",
            "--max-media-processes",
            "8",
            "--max-service-media-processes",
            "8",
            "--video-decode-workers",
            "6",
            "--audio-decode-workers",
            "2",
        ]
    )

    assert args.max_media_processes == 8
    assert args.max_service_media_processes == 8
    assert args.video_decode_workers == 6
    assert args.audio_decode_workers == 2


def test_live_entrypoint_exposes_startup_admission_policy():
    module = importlib.import_module("live_main")

    args = module.parse_args(
        [
            "--command-worker",
            "--startup-mode",
            "full_snapshot",
            "--startup-lookback-segments",
            "7",
            "--catch-up-soft-lag-segments",
            "3",
            "--catch-up-recovery-lag-segments",
            "1",
            "--live-edge-hard-lag-segments",
            "8",
            "--live-edge-retention-segments",
            "3",
            "--catch-up-transition-cycles",
            "4",
        ]
    )

    assert args.startup_mode == "full_snapshot"
    assert args.startup_lookback_segments == 7
    assert args.catch_up_soft_lag_segments == 3.0
    assert args.catch_up_recovery_lag_segments == 1.0
    assert args.live_edge_hard_lag_segments == 8.0
    assert args.live_edge_retention_segments == 3
    assert args.catch_up_transition_cycles == 4


def test_live_entrypoint_exposes_explicit_variant_selection():
    module = importlib.import_module("live_main")

    args = module.parse_args(
        [
            "--command-worker",
            "--variant-selection",
            "explicit",
            "--variant-id",
            "720p",
            "--variant-id",
            "stable-1080",
        ]
    )

    assert args.variant_selection == "explicit"
    assert args.variant_id == ["720p", "stable-1080"]


def test_live_entrypoint_exposes_opt_in_freeze_options():
    module = importlib.import_module("live_main")

    args = module.parse_args(
        [
            "--url",
            "https://example.test/master.m3u8",
            "--enable-video-freeze",
            "--freeze-noise-db",
            "-50",
            "--freeze-detector-minimum-duration",
            "0.4",
            "--freeze-warning-duration",
            "4",
            "--freeze-alert-duration",
            "7",
        ]
    )

    assert args.enable_video_freeze is True
    assert args.freeze_noise_db == -50.0
    assert args.freeze_detector_minimum_duration == 0.4
    assert args.freeze_warning_duration == 4.0
    assert args.freeze_alert_duration == 7.0
