from __future__ import annotations

from datetime import datetime, timezone


class CommandGuardrails:
    """Pre-admission checks for command payload size and age to prevent memory bloat and stale execution."""

    def __init__(
        self,
        *,
        max_payload_bytes: int = 65_536,
        max_command_age_seconds: float | None = None,
        future_tolerance_seconds: float = 30.0,
    ) -> None:
        if max_payload_bytes <= 0:
            raise ValueError(f"max_payload_bytes must be > 0, got {max_payload_bytes}")
        if max_command_age_seconds is not None and max_command_age_seconds <= 0:
            raise ValueError(
                f"max_command_age_seconds must be > 0 or None, got {max_command_age_seconds}"
            )
        self.max_payload_bytes = max_payload_bytes
        self.max_command_age_seconds = max_command_age_seconds
        self.future_tolerance_seconds = max(0.0, float(future_tolerance_seconds))

    def check_payload_size(self, raw_payload: str | bytes) -> tuple[bool, int]:
        if isinstance(raw_payload, bytes):
            size_bytes = len(raw_payload)
        else:
            size_bytes = len(str(raw_payload).encode("utf-8"))

        is_valid = size_bytes <= self.max_payload_bytes
        return is_valid, size_bytes

    def check_command_age(
        self,
        requested_at: datetime,
        now: datetime | None = None,
        *,
        is_reclaimed: bool = False,
    ) -> tuple[bool, str | None]:
        if self.max_command_age_seconds is None:
            return True, None

        if is_reclaimed:
            # Reclaimed commands during restart recovery should not be rejected on age
            return True, None

        current_now = now or datetime.now(timezone.utc)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=timezone.utc)
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)

        age_seconds = (current_now - requested_at).total_seconds()

        # Reject if in the far future beyond tolerance
        if age_seconds < -self.future_tolerance_seconds:
            return False, "FUTURE_COMMAND"

        if age_seconds > self.max_command_age_seconds:
            return False, "STALE_COMMAND"

        return True, None
