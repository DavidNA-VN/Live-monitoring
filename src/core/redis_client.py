from dataclasses import dataclass
import os

import redis


class RedisUnavailableError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class RedisSettings:
    url: str

    socket_connect_timeout: float = 1.0
    socket_timeout: float = 5.0
    health_check_interval: int = 30

    @classmethod
    def from_env(
        cls,
    ) -> "RedisSettings":

        return cls(
            url=os.getenv(
                "REDIS_URL",
                "redis://localhost:6379/0",
            ),
            socket_connect_timeout=float(
                os.getenv(
                    "REDIS_CONNECT_TIMEOUT",
                    "1.0",
                )
            ),
            socket_timeout=float(
                os.getenv(
                    "REDIS_SOCKET_TIMEOUT",
                    "5.0",
                )
            ),
            health_check_interval=int(
                os.getenv(
                    "REDIS_HEALTH_CHECK_INTERVAL",
                    "30",
                )
            ),
        )


class RedisClient:

    def __init__(
        self,
        settings: RedisSettings | None = None,
    ):
        self.settings = (
            settings
            or RedisSettings.from_env()
        )

        self.client = redis.Redis.from_url(
            self.settings.url,
            decode_responses=True,
            socket_connect_timeout=(
                self.settings
                .socket_connect_timeout
            ),
            socket_timeout=(
                self.settings.socket_timeout
            ),
            health_check_interval=(
                self.settings
                .health_check_interval
            ),
        )

    def ping(
        self,
    ) -> None:

        try:
            self.client.ping()
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Redis unavailable: {exc}"
            ) from exc

    def close(
        self,
    ) -> None:

        self.client.close()