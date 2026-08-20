from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ContextManager, Protocol
from urllib.parse import urlsplit

import httpx

from media.errors import (
    UnsupportedMediaInputError,
)
from media.resource_reader import MediaResourceReader
from models.segment import Segment


@dataclass(frozen=True)
class ResolvedMediaInput:
    uri: str
    ffmpeg_input_options: tuple[str, ...] = ()
    materialized: bool = False


class MediaInputResolver(Protocol):
    def open(
        self,
        segment: Segment,
    ) -> ContextManager[ResolvedMediaInput]:
        ...


class HlsMediaInputResolver:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        request_headers: Mapping[str, str] | None = None,
        max_materialized_bytes: int = 64 * 1024 * 1024,
        temp_directory: str | None = None,
        http_client: httpx.Client | None = None,
    ):
        if timeout <= 0:
            raise ValueError("timeout must be > 0")

        if max_materialized_bytes <= 0:
            raise ValueError(
                "max_materialized_bytes must be > 0"
            )

        self.request_headers = dict(
            request_headers or {}
        )
        self.temp_directory = temp_directory
        self.resource_reader = MediaResourceReader(
            timeout=timeout,
            request_headers=self.request_headers,
            max_bytes=max_materialized_bytes,
            http_client=http_client,
        )
        self._ffmpeg_header_options()

    def close(self) -> None:
        self.resource_reader.close()

    @contextmanager
    def open(
        self,
        segment: Segment,
    ) -> Iterator[ResolvedMediaInput]:
        self._validate_encryption(segment)

        requires_materialization = (
            segment.byte_range is not None
            or segment.init_section is not None
        )

        if not requires_materialization:
            yield ResolvedMediaInput(
                uri=segment.uri,
                ffmpeg_input_options=(
                    self._ffmpeg_header_options()
                ),
            )
            return

        payload_parts: list[bytes] = []

        if segment.init_section is not None:
            payload_parts.append(
                self.resource_reader.read(
                    segment.init_section.uri,
                    segment.init_section.byte_range,
                )
            )

        payload_parts.append(
            self.resource_reader.read(
                segment.uri,
                segment.byte_range,
            )
        )

        payload = b"".join(payload_parts)
        self.resource_reader.check_size(
            len(payload),
            context="combined media input",
        )

        suffix = self._temporary_suffix(segment)

        temporary = NamedTemporaryFile(
            mode="wb",
            prefix="media-monitor-",
            suffix=suffix,
            dir=self.temp_directory,
            delete=False,
        )
        temporary_path = Path(temporary.name)

        try:
            with temporary:
                temporary.write(payload)

            yield ResolvedMediaInput(
                uri=str(temporary_path),
                materialized=True,
            )
        finally:
            temporary_path.unlink(
                missing_ok=True
            )

    @staticmethod
    def _validate_encryption(
        segment: Segment,
    ) -> None:
        if segment.encryption is None:
            return

        raise UnsupportedMediaInputError(
            (
                "Encrypted HLS segment is not supported "
                "by the segment resolver yet: "
                f"method={segment.encryption.method}"
            )
        )

    def _ffmpeg_header_options(self) -> tuple[str, ...]:
        if not self.request_headers:
            return ()

        header_lines: list[str] = []

        for name, value in self.request_headers.items():
            if any(
                character in name or character in value
                for character in ("\r", "\n")
            ):
                raise ValueError(
                    "HTTP headers must not contain newlines"
                )

            header_lines.append(
                f"{name}: {value}\r\n"
            )

        return (
            "-headers",
            "".join(header_lines),
        )

    @staticmethod
    def _temporary_suffix(
        segment: Segment,
    ) -> str:
        if segment.init_section is not None:
            return ".mp4"

        suffix = Path(
            urlsplit(segment.uri).path
        ).suffix

        return suffix or ".bin"
