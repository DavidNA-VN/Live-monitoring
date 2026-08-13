from checks.audio_loss.check import AudioLossCheck
from checks.black_screen.check import BlackScreenCheck
from checks.freeze_frame.check import FreezeFrameCheck
from core.monitor import MediaCheck


def default_checks() -> list[MediaCheck]:
    return [
        BlackScreenCheck(),
        FreezeFrameCheck(),
        AudioLossCheck(),
    ]


def get_check_names(
    checks: list[MediaCheck],
) -> list[str]:
    return [
        check.name
        for check in checks
    ]
