from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import httpx

from media.errors import (
    MediaInputFetchError,
    UnsupportedMediaInputError,
)
from models.segment import ByteRange


class MediaResourceReader:
    def __init__(
        self,
        *,
        timeout: float,
        request_headers: Mapping[str, str],
        max_bytes: int,
        http_client: httpx.Client | None = None,
    ):
        self.timeout = timeout
        self.request_headers = dict(request_headers)
        self.max_bytes = max_bytes
        self.http_client = http_client
        self._owns_http_client = http_client is None

    def close(self) -> None:
        if (
            self._owns_http_client
            and self.http_client is not None
        ):
            self.http_client.close()
            self.http_client = None

    def read(
        self,
        uri: str,
        byte_range: ByteRange | None,
    ) -> bytes:
        parsed = urlsplit(uri)

        if parsed.scheme in ("", "file"):
            return self._read_local(
                uri,
                byte_range,
            )

        if parsed.scheme not in ("http", "https"):
            raise UnsupportedMediaInputError(
                (
                    "Unsupported media URI scheme: "
                    f"{parsed.scheme or '<empty>'}"
                )
            )

        return self._read_http(
            uri,
            byte_range,
        )

    def _read_http(
        self,
        uri: str,
        byte_range: ByteRange | None,
    ) -> bytes:
        headers = dict(self.request_headers)

        if byte_range is not None:
            headers["Range"] = (
                f"bytes={byte_range.offset}-"
                f"{byte_range.end_exclusive - 1}"
            )

        try:
            response_context = self._client().stream(
                "GET",
                uri,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise MediaInputFetchError(
                f"Unable to fetch media resource {uri}: {exc}"
            ) from exc

        try:
            with response_context as response:
                if not 200 <= response.status_code < 300:
                    raise MediaInputFetchError(
                        (
                            "Media resource returned HTTP "
                            f"{response.status_code}: {uri}"
                        )
                    )

                content = self._read_bounded_response(
                    response
                )
        except httpx.HTTPError as exc:
            raise MediaInputFetchError(
                f"Unable to read media resource {uri}: {exc}"
            ) from exc

        if byte_range is not None:
            content = self._select_http_range(
                content=content,
                status_code=response.status_code,
                byte_range=byte_range,
                uri=uri,
            )

        self.check_size(
            len(content),
            context=uri,
        )

        return content

    def _read_bounded_response(
        self,
        response,
    ) -> bytes:
        payload = bytearray()

        for chunk in response.iter_bytes():
            remaining = (
                self.max_bytes + 1 - len(payload)
            )
            payload.extend(chunk[:remaining])

            if (
                len(chunk) > remaining
                or len(payload) > self.max_bytes
            ):
                break

        self.check_size(
            len(payload),
            context="HTTP response",
        )

        return bytes(payload)

    def _client(self) -> httpx.Client:
        if self.http_client is None:
            self.http_client = httpx.Client(
                follow_redirects=True
            )

        return self.http_client

    def _read_local(
        self,
        uri: str,
        byte_range: ByteRange | None,
    ) -> bytes:
        parsed = urlsplit(uri)
        path = Path(
            url2pathname(
                unquote(parsed.path)
            )
            if parsed.scheme == "file"
            else uri
        )

        try:
            with path.open("rb") as source:
                if byte_range is not None:
                    source.seek(byte_range.offset)
                    content = source.read(
                        byte_range.length
                    )
                else:
                    content = source.read(
                        self.max_bytes + 1
                    )
        except OSError as exc:
            raise MediaInputFetchError(
                f"Unable to read media resource {uri}: {exc}"
            ) from exc

        if (
            byte_range is not None
            and len(content) != byte_range.length
        ):
            raise MediaInputFetchError(
                (
                    "Media byte range was shorter than "
                    f"declared for {uri}"
                )
            )

        self.check_size(
            len(content),
            context=uri,
        )

        return content

    @staticmethod
    def _select_http_range(
        *,
        content: bytes,
        status_code: int,
        byte_range: ByteRange,
        uri: str,
    ) -> bytes:
        if status_code == 206:
            selected = content[:byte_range.length]
        elif len(content) == byte_range.length:
            selected = content
        elif len(content) >= byte_range.end_exclusive:
            selected = content[
                byte_range.offset:
                byte_range.end_exclusive
            ]
        else:
            selected = b""

        if len(selected) != byte_range.length:
            raise MediaInputFetchError(
                (
                    "Media server did not return the "
                    f"requested byte range for {uri}"
                )
            )

        return selected

    def check_size(
        self,
        size: int,
        *,
        context: str,
    ) -> None:
        if size > self.max_bytes:
            raise MediaInputFetchError(
                (
                    "Materialized media input exceeds "
                    f"{self.max_bytes} bytes: {context}"
                )
            )
