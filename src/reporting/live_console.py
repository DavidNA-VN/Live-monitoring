import logging
import os
import socket
from datetime import datetime, timezone
from uuid import uuid4

import redis

from core.redis_client import (
    RedisClient,
)
from core.redis_keys import AlertRedisKeys


logger = logging.getLogger(__name__)


class LiveAlertConsole:

    def __init__(
        self,
        redis_client: RedisClient,
        alert_keys: AlertRedisKeys | None = None,
        group_name: str = "live-console-v1",
        consumer_name: str | None = None,
        block_ms: int = 1000,
        reclaim_idle_ms: int = 30_000,
        max_deliveries: int = 5,
        dead_letter_max_length: int = 1_000,
    ):
        if max_deliveries <= 0:
            raise ValueError("max_deliveries must be > 0")
        if dead_letter_max_length <= 0:
            raise ValueError("dead_letter_max_length must be > 0")
        self.redis = redis_client.client

        self.keys = alert_keys or AlertRedisKeys()

        self.group_name = group_name

        self.consumer_name = (
            consumer_name
            or self._default_consumer_name()
        )

        self.block_ms = block_ms
        self.reclaim_idle_ms = (
            reclaim_idle_ms
        )
        self.max_deliveries = max_deliveries
        self.dead_letter_max_length = dead_letter_max_length

    def run(
        self,
        stop_event,
    ) -> None:

        stream_key = (
            self.keys.outbox()
        )

        self._ensure_group(
            stream_key
        )

        while not stop_event.is_set():

            try:
                self._recover_pending(
                    stream_key
                )

                messages = (
                    self.redis.xreadgroup(
                        groupname=self.group_name,
                        consumername=(
                            self.consumer_name
                        ),
                        streams={
                            stream_key: ">"
                        },
                        count=100,
                        block=self.block_ms,
                    )
                )

            except redis.RedisError as exc:
                logger.warning(
                    "Alert outbox unavailable: %s",
                    exc,
                )

                stop_event.wait(1.0)
                continue

            self._consume_messages(
                stream_key=stream_key,
                messages=messages,
            )

    def _ensure_group(
        self,
        stream_key: str,
    ) -> None:

        try:
            self.redis.xgroup_create(
                name=stream_key,
                groupname=self.group_name,
                id="0-0",
                mkstream=True,
            )

        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _recover_pending(
        self,
        stream_key: str,
    ) -> None:

        try:
            result = self.redis.xautoclaim(
                name=stream_key,
                groupname=self.group_name,
                consumername=(
                    self.consumer_name
                ),
                min_idle_time=(
                    self.reclaim_idle_ms
                ),
                start_id="0-0",
                count=100,
            )

        except redis.ResponseError:
            return

        if not result:
            return

        entries = (
            result[1]
            if len(result) > 1
            else []
        )

        if not entries:
            return

        self._consume_entries(
            stream_key=stream_key,
            entries=entries,
        )

    def _consume_messages(
        self,
        stream_key: str,
        messages,
    ) -> None:

        for _, entries in messages:
            self._consume_entries(
                stream_key=stream_key,
                entries=entries,
            )

    def _consume_entries(
        self,
        stream_key: str,
        entries,
    ) -> None:

        for message_id, fields in entries:

            try:
                self._print_alert(
                    fields
                )

            except Exception as exc:
                logger.exception(
                    (
                        "Alert consumer failed "
                        "message_id=%s"
                    ),
                    message_id,
                )

                self._handle_failure(
                    stream_key=stream_key,
                    message_id=message_id,
                    fields=fields,
                    error=str(exc),
                )

                # Không ACK.
                # Message vẫn ở PEL và sẽ được reclaim.
                continue

            self.redis.xack(
                stream_key,
                self.group_name,
                message_id,
            )

    def _handle_failure(
        self,
        *,
        stream_key: str,
        message_id: str,
        fields: dict[str, str],
        error: str,
    ) -> None:
        try:
            pending = self.redis.xpending_range(
                stream_key,
                self.group_name,
                min=message_id,
                max=message_id,
                count=1,
            )
            deliveries = (
                int(pending[0].get("times_delivered", 1))
                if pending
                else 1
            )
            if deliveries < self.max_deliveries:
                return
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.xadd(
                self.keys.dead_letter(),
                {
                    **fields,
                    "source_stream": stream_key,
                    "source_message_id": message_id,
                    "consumer_group": self.group_name,
                    "delivery_count": str(deliveries),
                    "consumer_error": error,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
                maxlen=self.dead_letter_max_length,
                approximate=True,
            )
            pipeline.xack(stream_key, self.group_name, message_id)
            pipeline.execute()
        except redis.RedisError:
            logger.exception(
                "Unable to dead-letter alert message_id=%s",
                message_id,
            )

    @staticmethod
    def _default_consumer_name(
    ) -> str:

        return (
            f"{socket.gethostname()}:"
            f"{os.getpid()}:"
            f"{uuid4().hex[:8]}"
        )

    @staticmethod
    def _print_alert(
        fields: dict[str, str],
    ) -> None:

        event_type = fields.get(
            "type",
            "UNKNOWN",
        )

        state = fields.get(
            "state",
            "UNKNOWN",
        )

        variant = fields.get(
            "variant_id",
            "unknown",
        )

        event_id = fields.get(
            "event_id",
            "unknown",
        )

        if event_type == "BLACK_SCREEN":
            duration = fields.get(
                "duration",
                "?",
            )

            print(
                (
                    f"[BLACK_SCREEN:{state}] "
                    f"variant={variant} "
                    f"duration={duration}s "
                    f"event={event_id}"
                ),
                flush=True,
            )

            return

        if (
            event_type
            == "REPEATED_BLACK_SCREEN"
        ):
            occurrences = fields.get(
                "occurrences",
                "?",
            )

            total_black = fields.get(
                "total_black_duration",
                "?",
            )

            window = fields.get(
                "window_seconds"
            )

            window_text = (
                f" window={window}s"
                if window is not None
                else ""
            )

            print(
                (
                    "[REPEATED_BLACK_SCREEN:"
                    f"{state}] "
                    f"variant={variant} "
                    f"occurrences={occurrences} "
                    f"total_black={total_black}s"
                    f"{window_text} "
                    f"incident={event_id}"
                ),
                flush=True,
            )

            return

        if event_type == "RUNTIME_HEALTH":
            reasons = fields.get(
                "reasons",
                "",
            )

            print(
                (
                    f"[RUNTIME:{state}] "
                    f"reasons={reasons}"
                ),
                flush=True,
            )

            return

        print(
            (
                f"[{event_type}:{state}] "
                f"variant={variant} "
                f"event={event_id}"
            ),
            flush=True,
        )
