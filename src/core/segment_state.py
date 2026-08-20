from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import redis
from models.processing import SegmentProcessingRecord
from core.redis_client import (
    RedisClient,
    RedisUnavailableError,
)
from core.redis_keys import RedisKeyBuilder
from core.redis_scripts import (
    COMPLETE_SEGMENT,
    RELEASE_OWNED_LOCK,
    RENEW_OWNED_LOCK,
)
from models.processing import (
    SegmentClaim,
    SegmentClaimStatus,
    SegmentProcessingIdentity,
    SegmentProcessingStatus,
)


class SegmentLeaseLostError(
    RuntimeError
):
    pass


class RedisSegmentStateStore:

    def __init__(
        self,
        redis_client: RedisClient,
        key_builder: RedisKeyBuilder | None = None,
        lease_ms: int = 90_000,
        state_ttl_seconds: int = 21_600,
        max_attempts: int = 3,
    ):
        if lease_ms <= 0:
            raise ValueError(
                "lease_ms must be > 0"
            )

        if state_ttl_seconds <= 0:
            raise ValueError(
                "state_ttl_seconds must be > 0"
            )

        if max_attempts <= 0:
            raise ValueError(
                "max_attempts must be > 0"
            )

        self.redis_client = redis_client
        self.redis = redis_client.client

        self.key_builder = (
            key_builder
            or RedisKeyBuilder()
        )

        self.lease_ms = lease_ms
        self.state_ttl_seconds = (
            state_ttl_seconds
        )
        self.max_attempts = max_attempts
    def get_records(
        self,
        identities: list[
            SegmentProcessingIdentity
        ],
    ) -> dict[
        SegmentProcessingIdentity,
        SegmentProcessingRecord,
    ]:

        if not identities:
            return {}

        try:
            pipeline = self.redis.pipeline(
                transaction=False
            )

            for identity in identities:
                state_key = (
                    self.key_builder.segment_state(
                        identity
                    )
                )

                pipeline.hmget(
                    state_key,
                    (
                        "status",
                        "attempts",
                        "updated_at",
                        "last_error",
                    ),
                )

            raw_records = pipeline.execute()

        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to read segment "
                    f"processing states: {exc}"
                )
            ) from exc

        records = {}

        for identity, raw in zip(
            identities,
            raw_records,
        ):
            (
                status_value,
                attempts_value,
                updated_at,
                last_error,
            ) = raw

            status = None

            if status_value:
                try:
                    status = SegmentProcessingStatus(
                        status_value
                    )
                except ValueError:
                    status = None

            records[identity] = (
                SegmentProcessingRecord(
                    identity=identity,
                    status=status,
                    attempts=self._parse_int(
                        attempts_value,
                        default=0,
                    ),
                    updated_at=updated_at,
                    last_error=(
                        last_error or None
                    ),
                )
            )

        return records
    def claim(
        self,
        identity: SegmentProcessingIdentity,
    ) -> SegmentClaim:

        state_key = (
            self.key_builder.segment_state(
                identity
            )
        )

        lock_key = (
            self.key_builder.segment_lock(
                identity
            )
        )

        lease_token = uuid4().hex

        try:
            acquired = self.redis.set(
                lock_key,
                lease_token,
                nx=True,
                px=self.lease_ms,
            )

            if not acquired:
                return SegmentClaim(
                    identity=identity,
                    status=(
                        SegmentClaimStatus.BUSY
                    ),
                )

            state = self.redis.hgetall(
                state_key
            )

            status = state.get(
                "status"
            )

            attempts = self._parse_int(
                state.get("attempts"),
                default=0,
            )

            if (
                status
                == SegmentProcessingStatus.SUCCESS.value
            ):
                self._release_lock(
                    lock_key=lock_key,
                    lease_token=lease_token,
                )

                return SegmentClaim(
                    identity=identity,
                    status=(
                        SegmentClaimStatus
                        .ALREADY_SUCCESSFUL
                    ),
                    attempt=attempts,
                )

            if (
                status
                == SegmentProcessingStatus
                .FAILED_TERMINAL.value
            ):
                self._release_lock(
                    lock_key=lock_key,
                    lease_token=lease_token,
                )

                return SegmentClaim(
                    identity=identity,
                    status=(
                        SegmentClaimStatus
                        .TERMINAL_FAILURE
                    ),
                    attempt=attempts,
                )

            if attempts >= self.max_attempts:
                self._release_lock(
                    lock_key=lock_key,
                    lease_token=lease_token,
                )

                return SegmentClaim(
                    identity=identity,
                    status=(
                        SegmentClaimStatus
                        .RETRY_EXHAUSTED
                    ),
                    attempt=attempts,
                )

            now = self._now()

            next_attempt = attempts + 1

            mapping = {
                "status": (
                    SegmentProcessingStatus
                    .PROCESSING.value
                ),
                "attempts": str(
                    next_attempt
                ),
                "updated_at": now,
                "lease_token": lease_token,
                "last_error": "",
            }

            if not state.get(
                "first_seen_at"
            ):
                mapping[
                    "first_seen_at"
                ] = now

            self.redis.hset(
                state_key,
                mapping=mapping,
            )

            self.redis.expire(
                state_key,
                self.state_ttl_seconds,
            )

            return SegmentClaim(
                identity=identity,
                status=(
                    SegmentClaimStatus.ACQUIRED
                ),
                lease_token=lease_token,
                attempt=next_attempt,
            )

        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to claim segment "
                    f"{identity.sequence}: {exc}"
                )
            ) from exc

    def mark_success(
        self,
        claim: SegmentClaim,
    ) -> None:

        self._complete(
            claim=claim,
            status=(
                SegmentProcessingStatus.SUCCESS
            ),
            error="",
        )

    def mark_retryable_failure(
        self,
        claim: SegmentClaim,
        error: str,
    ) -> None:

        self._complete(
            claim=claim,
            status=(
                SegmentProcessingStatus
                .FAILED_RETRYABLE
            ),
            error=error,
        )

    def mark_terminal_failure(
        self,
        claim: SegmentClaim,
        error: str,
    ) -> None:

        self._complete(
            claim=claim,
            status=(
                SegmentProcessingStatus
                .FAILED_TERMINAL
            ),
            error=error,
        )

    def renew(
        self,
        claim: SegmentClaim,
    ) -> None:

        lease_token = self._require_token(
            claim
        )

        lock_key = (
            self.key_builder.segment_lock(
                claim.identity
            )
        )

        try:
            renewed = self.redis.eval(
                RENEW_OWNED_LOCK,
                1,
                lock_key,
                lease_token,
                self.lease_ms,
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to renew segment "
                    f"lease: {exc}"
                )
            ) from exc

        if int(renewed or 0) != 1:
            raise SegmentLeaseLostError(
                (
                    "Segment processing lease "
                    "was lost before renewal."
                )
            )

    def release(
        self,
        claim: SegmentClaim,
    ) -> None:

        lease_token = self._require_token(
            claim
        )

        lock_key = (
            self.key_builder.segment_lock(
                claim.identity
            )
        )

        self._release_lock(
            lock_key=lock_key,
            lease_token=lease_token,
        )

    def _complete(
        self,
        claim: SegmentClaim,
        status: SegmentProcessingStatus,
        error: str,
    ) -> None:

        lease_token = self._require_token(
            claim
        )

        lock_key = (
            self.key_builder.segment_lock(
                claim.identity
            )
        )

        state_key = (
            self.key_builder.segment_state(
                claim.identity
            )
        )

        try:
            completed = self.redis.eval(
                COMPLETE_SEGMENT,
                2,
                lock_key,
                state_key,
                lease_token,
                status.value,
                self._now(),
                error,
                self.state_ttl_seconds,
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to update segment "
                    f"state: {exc}"
                )
            ) from exc

        if int(completed or 0) != 1:
            raise SegmentLeaseLostError(
                (
                    "Segment processing result "
                    "was rejected because the "
                    "worker no longer owns the lease."
                )
            )

    def _release_lock(
        self,
        lock_key: str,
        lease_token: str,
    ) -> None:

        try:
            self.redis.eval(
                RELEASE_OWNED_LOCK,
                1,
                lock_key,
                lease_token,
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                (
                    "Unable to release segment "
                    f"lease: {exc}"
                )
            ) from exc

    @staticmethod
    def _require_token(
        claim: SegmentClaim,
    ) -> str:

        if (
            not claim.acquired
            or claim.lease_token is None
        ):
            raise ValueError(
                (
                    "Operation requires an "
                    "acquired segment claim."
                )
            )

        return claim.lease_token

    @staticmethod
    def _parse_int(
        value: str | None,
        default: int,
    ) -> int:

        if value is None:
            return default

        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    def _now() -> str:

        return datetime.now(
            timezone.utc
        ).isoformat()
