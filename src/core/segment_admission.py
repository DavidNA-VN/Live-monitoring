from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from time import monotonic
from typing import Callable, Iterable, Protocol

from models.segment import Segment


@dataclass(frozen=True)
class ProfileSegmentIdentity:
    profile_name: str
    variant_stable_id: str
    timeline_generation: int
    discontinuity_sequence: int
    sequence: int
    media_revision: str


@dataclass(frozen=True)
class AdmittedProfileSegment:
    identity: ProfileSegmentIdentity
    segment: Segment
    admitted_at: float
    last_seen_at: float


class AdmissionDropReason(str, Enum):
    EXPIRED = "expired"
    CAPACITY = "capacity"


@dataclass(frozen=True)
class AdmissionDrop:
    identity: ProfileSegmentIdentity
    reason: AdmissionDropReason


@dataclass(frozen=True)
class AdmissionResult:
    admitted: int = 0
    refreshed: int = 0
    suppressed: int = 0
    drops: tuple[AdmissionDrop, ...] = ()


class AdmissionQueue(Protocol):
    def admit(
        self,
        *,
        profile_name: str,
        segments: Iterable[Segment],
    ) -> AdmissionResult:
        ...

    def expire(self) -> tuple[AdmissionDrop, ...]:
        ...

    def snapshot(self) -> tuple[AdmittedProfileSegment, ...]:
        ...

    def acknowledge(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        ...

    def protect(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        ...

    def release(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        ...

    @property
    def depth(self) -> int:
        ...

    @property
    def oldest_age_seconds(self) -> float:
        ...


class SegmentAdmissionQueue:
    """Bounded live-work retention independent from playlist windows."""

    def __init__(
        self,
        *,
        max_items: int,
        max_age_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be > 0")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be > 0")

        self.max_items = max_items
        self.max_age_seconds = max_age_seconds
        self.clock = clock
        self._items: OrderedDict[
            ProfileSegmentIdentity,
            AdmittedProfileSegment,
        ] = OrderedDict()
        self._suppressed_until: dict[
            ProfileSegmentIdentity,
            float,
        ] = {}
        self._protected: set[ProfileSegmentIdentity] = set()
        self._lock = Lock()

    def admit(
        self,
        *,
        profile_name: str,
        segments: Iterable[Segment],
    ) -> AdmissionResult:
        now = self.clock()
        admitted = 0
        refreshed = 0
        suppressed = 0
        drops: list[AdmissionDrop] = []

        with self._lock:
            self._purge_suppression(now)
            drops.extend(self._expire(now))

            for segment in segments:
                identity = self.identity_for(
                    profile_name=profile_name,
                    segment=segment,
                )
                existing = self._items.get(identity)
                if existing is not None:
                    self._items[identity] = AdmittedProfileSegment(
                        identity=identity,
                        segment=segment,
                        admitted_at=existing.admitted_at,
                        last_seen_at=now,
                    )
                    refreshed += 1
                    continue

                if identity in self._suppressed_until:
                    suppressed += 1
                    continue

                self._items[identity] = AdmittedProfileSegment(
                    identity=identity,
                    segment=segment,
                    admitted_at=now,
                    last_seen_at=now,
                )
                admitted += 1

            drops.extend(self._enforce_capacity(now))

        return AdmissionResult(
            admitted=admitted,
            refreshed=refreshed,
            suppressed=suppressed,
            drops=tuple(drops),
        )

    def expire(self) -> tuple[AdmissionDrop, ...]:
        now = self.clock()
        with self._lock:
            self._purge_suppression(now)
            return tuple(self._expire(now))

    def snapshot(self) -> tuple[AdmittedProfileSegment, ...]:
        with self._lock:
            return tuple(self._items.values())

    def acknowledge(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        now = self.clock()
        with self._lock:
            for identity in identities:
                if self._items.pop(identity, None) is not None:
                    self._suppress(identity, now)

    def protect(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        with self._lock:
            self._protected.update(identities)

    def release(
        self,
        identities: Iterable[ProfileSegmentIdentity],
    ) -> None:
        with self._lock:
            self._protected.difference_update(identities)

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def oldest_age_seconds(self) -> float:
        now = self.clock()
        with self._lock:
            if not self._items:
                return 0.0
            oldest = min(
                item.admitted_at for item in self._items.values()
            )
            return max(0.0, now - oldest)

    @staticmethod
    def identity_for(
        *,
        profile_name: str,
        segment: Segment,
    ) -> ProfileSegmentIdentity:
        return ProfileSegmentIdentity(
            profile_name=profile_name,
            variant_stable_id=segment.variant_stable_id,
            discontinuity_sequence=segment.discontinuity_sequence,
            sequence=segment.sequence,
            timeline_generation=segment.timeline_generation,
            media_revision=segment.media_revision,
        )

    def _expire(self, now: float) -> list[AdmissionDrop]:
        drops: list[AdmissionDrop] = []
        for identity, item in tuple(self._items.items()):
            if identity in self._protected:
                continue
            if now - item.admitted_at < self.max_age_seconds:
                continue
            del self._items[identity]
            self._suppress(identity, now)
            drops.append(
                AdmissionDrop(identity, AdmissionDropReason.EXPIRED)
            )
        return drops

    def _enforce_capacity(self, now: float) -> list[AdmissionDrop]:
        drops: list[AdmissionDrop] = []
        while len(self._items) > self.max_items:
            identity = next(
                (
                    candidate
                    for candidate in self._items
                    if candidate not in self._protected
                ),
                None,
            )
            if identity is None:
                break
            del self._items[identity]
            self._suppress(identity, now)
            drops.append(
                AdmissionDrop(identity, AdmissionDropReason.CAPACITY)
            )
        return drops

    def _suppress(
        self,
        identity: ProfileSegmentIdentity,
        now: float,
    ) -> None:
        self._suppressed_until[identity] = now + self.max_age_seconds

    def _purge_suppression(self, now: float) -> None:
        for identity, deadline in tuple(self._suppressed_until.items()):
            if deadline <= now:
                del self._suppressed_until[identity]
