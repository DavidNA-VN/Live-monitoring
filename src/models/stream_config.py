from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from models.analysis import (
    AnalysisResourceClass,
    ResourcePoolLimit,
    default_resource_limits,
)
from models.stream import StreamIdentity, build_stream_identity


@dataclass(frozen=True)
class StreamConfig:
    master_url: str
    stream_id: str | None = None
    enabled: bool = True
    request_headers: Mapping[str, str] | None = None
    playlist_timeout: float = 5.0
    resource_limits: Mapping[
        AnalysisResourceClass,
        ResourcePoolLimit,
    ] = field(default_factory=default_resource_limits)
    max_concurrent_media_processes: int = 4
    max_admitted_work: int = 2048
    max_work_age_seconds: float = 120.0
    max_segments_per_batch: int = 20
    media_playlist_workers: int = 4
    alert_stream_max_length: int = 10_000

    def __post_init__(self) -> None:
        if not self.master_url.strip():
            raise ValueError("master_url must not be empty")
        positive = {
            "playlist_timeout": self.playlist_timeout,
            "max_concurrent_media_processes": (
                self.max_concurrent_media_processes
            ),
            "max_admitted_work": self.max_admitted_work,
            "max_work_age_seconds": self.max_work_age_seconds,
            "max_segments_per_batch": self.max_segments_per_batch,
            "media_playlist_workers": self.media_playlist_workers,
            "alert_stream_max_length": self.alert_stream_max_length,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
        normalized_limits = {}
        for resource_class, limit in self.resource_limits.items():
            normalized_class = AnalysisResourceClass(resource_class)
            if not isinstance(limit, ResourcePoolLimit):
                raise TypeError(
                    f"Invalid resource limit for {normalized_class.value}"
                )
            normalized_limits[normalized_class] = limit
        object.__setattr__(
            self,
            "resource_limits",
            MappingProxyType(normalized_limits),
        )
        if self.request_headers is not None:
            object.__setattr__(
                self, "request_headers", dict(self.request_headers)
            )

    @property
    def identity(self) -> StreamIdentity:
        return build_stream_identity(
            master_url=self.master_url,
            stream_id=self.stream_id,
        )
