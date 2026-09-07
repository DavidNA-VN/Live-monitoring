from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from types import MappingProxyType

from models.analysis import (
    AnalysisResourceClass,
    ResourcePoolLimit,
    default_resource_limits,
)
from models.admission import LiveAdmissionPolicy
from models.stream import StreamIdentity, build_stream_identity
from models.variant_selection import VariantSelectionPolicy


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
    admission_policy: LiveAdmissionPolicy = field(
        default_factory=LiveAdmissionPolicy
    )
    variant_selection: VariantSelectionPolicy = field(
        default_factory=VariantSelectionPolicy
    )
    alert_stream_max_length: int = 10_000
    black_screen_enabled: bool = True
    video_freeze_enabled: bool = False
    freeze_noise_db: float = -60.0
    freeze_detector_minimum_duration: float = 0.2
    freeze_warning_duration: float = 3.0
    freeze_alert_duration: float = 5.0
    audio_loss_enabled: bool = True
    silence_threshold_dbfs: float = -60.0
    audio_loss_duration: float = 30.0
    audio_track_index: int = 0

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
        if not any(
            (
                self.black_screen_enabled,
                self.video_freeze_enabled,
                self.audio_loss_enabled,
            )
        ):
            raise ValueError("At least one monitoring check must be enabled")
        freeze_values = {
            "freeze_detector_minimum_duration": (
                self.freeze_detector_minimum_duration
            ),
            "freeze_warning_duration": self.freeze_warning_duration,
            "freeze_alert_duration": self.freeze_alert_duration,
        }
        if not math.isfinite(self.freeze_noise_db) or self.freeze_noise_db > 0:
            raise ValueError("freeze_noise_db must be finite and <= 0")
        for name, value in freeze_values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if self.freeze_alert_duration <= self.freeze_warning_duration:
            raise ValueError(
                "freeze_alert_duration must be greater than "
                "freeze_warning_duration"
            )
        if (
            not math.isfinite(self.silence_threshold_dbfs)
            or self.silence_threshold_dbfs > 0
        ):
            raise ValueError(
                "silence_threshold_dbfs must be finite and <= 0"
            )
        if (
            not math.isfinite(self.audio_loss_duration)
            or self.audio_loss_duration <= 0
        ):
            raise ValueError("audio_loss_duration must be finite and > 0")
        if self.audio_track_index < 0:
            raise ValueError("audio_track_index must be >= 0")
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
        if not isinstance(self.admission_policy, LiveAdmissionPolicy):
            raise TypeError("admission_policy must be a LiveAdmissionPolicy")
        if not isinstance(self.variant_selection, VariantSelectionPolicy):
            raise TypeError("variant_selection must be a VariantSelectionPolicy")

    @property
    def identity(self) -> StreamIdentity:
        return build_stream_identity(
            master_url=self.master_url,
            stream_id=self.stream_id,
        )
