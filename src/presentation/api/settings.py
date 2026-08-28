from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Literal, Optional


@dataclass(frozen=True)
class ApiSettings:
    """Cấu hình khởi động và vận hành của Live Monitoring API."""

    mode: Literal["fake", "redis"] = "fake"
    redis_url: str = "redis://localhost:6379/0"
    redis_prefix: str = "media-monitor:v1"
    alert_history_scan_limit: int = 1000
    websocket_redis_block_ms: int = 1000
    enable_fake_generator: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("fake", "redis"):
            raise ValueError(
                f"Invalid MONITORING_API_MODE '{self.mode}'. Must be 'fake' or 'redis'."
            )
        if not self.redis_prefix or not self.redis_prefix.strip():
            raise ValueError("REDIS_PREFIX must not be empty")
        if self.alert_history_scan_limit <= 0:
            raise ValueError("ALERT_HISTORY_SCAN_LIMIT must be a positive integer (> 0)")
        if self.websocket_redis_block_ms <= 0:
            raise ValueError("WEBSOCKET_REDIS_BLOCK_MS must be a positive integer (> 0)")
        if self.mode == "redis" and (not self.redis_url or not self.redis_url.strip()):
            raise ValueError("REDIS_URL must not be empty when mode is 'redis'")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ApiSettings":
        source = os.environ if env is None else env

        raw_mode = source.get("MONITORING_API_MODE", "fake").strip().lower()
        raw_redis_url = source.get("REDIS_URL", "redis://localhost:6379/0").strip()
        raw_redis_prefix = source.get("REDIS_PREFIX", "media-monitor:v1").strip()

        try:
            history_limit = int(source.get("ALERT_HISTORY_SCAN_LIMIT", "1000"))
        except ValueError as exc:
            raise ValueError(
                f"ALERT_HISTORY_SCAN_LIMIT must be an integer: {exc}"
            ) from exc

        try:
            ws_block_ms = int(source.get("WEBSOCKET_REDIS_BLOCK_MS", "1000"))
        except ValueError as exc:
            raise ValueError(
                f"WEBSOCKET_REDIS_BLOCK_MS must be an integer: {exc}"
            ) from exc

        raw_fake_gen = source.get("ENABLE_FAKE_GENERATOR", "false").strip().lower()
        true_values = {"true", "1", "yes"}
        false_values = {"false", "0", "no"}
        if raw_fake_gen not in true_values | false_values:
            raise ValueError(
                "ENABLE_FAKE_GENERATOR must be one of: true, false, 1, 0, yes, no"
            )
        enable_fake_gen = raw_fake_gen in true_values

        return cls(
            mode=raw_mode,  # type: ignore[arg-type]
            redis_url=raw_redis_url,
            redis_prefix=raw_redis_prefix,
            alert_history_scan_limit=history_limit,
            websocket_redis_block_ms=ws_block_ms,
            enable_fake_generator=enable_fake_gen,
        )
