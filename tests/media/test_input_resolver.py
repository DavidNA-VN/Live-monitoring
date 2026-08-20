from pathlib import Path

import pytest

from media.errors import (
    MediaInputFetchError,
    UnsupportedMediaInputError,
)
from media.input_resolver import HlsMediaInputResolver
from models.segment import (
    ByteRange,
    MediaInitializationSection,
    SegmentEncryption,
)
from tests.factories.hls import make_segment


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self.content


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def stream(self, method, uri, *, headers, timeout):
        self.calls.append(
            {
                "method": method,
                "uri": uri,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.responses[uri]


def test_direct_segment_keeps_uri_and_passes_headers_to_ffmpeg():
    client = FakeHttpClient({})
    resolver = HlsMediaInputResolver(
        request_headers={"Authorization": "Bearer token"},
        http_client=client,
    )
    segment = make_segment(1)

    with resolver.open(segment) as media_input:
        assert media_input.uri == segment.uri
        assert media_input.materialized is False
        assert media_input.ffmpeg_input_options == (
            "-headers",
            "Authorization: Bearer token\r\n",
        )

    assert client.calls == []


def test_byte_range_is_materialized_and_removed_after_use():
    segment = make_segment(
        1,
        uri="https://media.test/shared.ts",
    )
    segment.byte_range = ByteRange(
        length=4,
        offset=2,
    )
    client = FakeHttpClient(
        {
            segment.uri: FakeResponse(
                b"abcdefghij"
            )
        }
    )
    resolver = HlsMediaInputResolver(
        http_client=client
    )

    with resolver.open(segment) as media_input:
        temporary_path = Path(media_input.uri)

        assert media_input.materialized is True
        assert temporary_path.read_bytes() == b"cdef"
        assert client.calls[0]["headers"]["Range"] == (
            "bytes=2-5"
        )

    assert temporary_path.exists() is False


def test_fmp4_combines_init_section_and_media_fragment():
    segment = make_segment(
        1,
        uri="https://media.test/segment.m4s",
    )
    segment.init_section = MediaInitializationSection(
        uri="https://media.test/init.mp4"
    )
    client = FakeHttpClient(
        {
            "https://media.test/init.mp4": FakeResponse(
                b"INIT"
            ),
            segment.uri: FakeResponse(b"FRAGMENT"),
        }
    )
    resolver = HlsMediaInputResolver(
        http_client=client
    )

    with resolver.open(segment) as media_input:
        temporary_path = Path(media_input.uri)

        assert temporary_path.suffix == ".mp4"
        assert temporary_path.read_bytes() == (
            b"INITFRAGMENT"
        )

    assert temporary_path.exists() is False


def test_encrypted_segment_is_explicitly_terminal():
    segment = make_segment(1)
    segment.encryption = SegmentEncryption(
        method="AES-128",
        key_uri="https://media.test/key.bin",
    )
    resolver = HlsMediaInputResolver(
        http_client=FakeHttpClient({})
    )

    with pytest.raises(
        UnsupportedMediaInputError,
        match="method=AES-128",
    ) as error:
        with resolver.open(segment):
            pass

    assert error.value.retryable is False


def test_http_failure_is_retryable():
    segment = make_segment(
        1,
        uri="https://media.test/shared.ts",
    )
    segment.byte_range = ByteRange(
        length=4,
        offset=0,
    )
    resolver = HlsMediaInputResolver(
        http_client=FakeHttpClient(
            {
                segment.uri: FakeResponse(
                    b"not found",
                    status_code=404,
                )
            }
        )
    )

    with pytest.raises(
        MediaInputFetchError,
        match="HTTP 404",
    ) as error:
        with resolver.open(segment):
            pass

    assert error.value.retryable is True


def test_materialized_http_response_is_bounded():
    segment = make_segment(
        1,
        uri="https://media.test/shared.ts",
    )
    segment.byte_range = ByteRange(
        length=8,
        offset=0,
    )
    resolver = HlsMediaInputResolver(
        max_materialized_bytes=4,
        http_client=FakeHttpClient(
            {
                segment.uri: FakeResponse(
                    b"response-larger-than-limit"
                )
            }
        ),
    )

    with pytest.raises(
        MediaInputFetchError,
        match="exceeds 4 bytes",
    ):
        with resolver.open(segment):
            pass
