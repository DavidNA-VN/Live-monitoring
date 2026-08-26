from dataclasses import dataclass
from hashlib import sha256


MAX_EXTERNAL_STREAM_ID_LENGTH = 128


@dataclass(frozen=True)
class StreamIdentity:
    """
    Stable identity of one monitored live channel/source.

    external_stream_id: Public business/channel identifier used by
    API, UI, logs, supervisor and public contracts.

    storage_id: Deterministic hash used for Redis keys, locks,
    processing state and event correlation.
    """

    external_stream_id: str
    storage_id: str
    master_url: str


def build_stream_identity(
    master_url: str,
    stream_id: str | None = None,
) -> StreamIdentity:
    if stream_id is not None:
        external_stream_id = stream_id.strip()
        if not external_stream_id:
            raise ValueError("stream_id must not be empty")
        if len(external_stream_id) > MAX_EXTERNAL_STREAM_ID_LENGTH:
            raise ValueError(
                "stream_id must not exceed "
                f"{MAX_EXTERNAL_STREAM_ID_LENGTH} characters"
            )

        storage_id = sha256(
            external_stream_id.encode("utf-8")
        ).hexdigest()[:24]
    else:
        storage_id = sha256(
            master_url.encode("utf-8")
        ).hexdigest()[:24]
        external_stream_id = storage_id

    return StreamIdentity(
        external_stream_id=external_stream_id,
        storage_id=storage_id,
        master_url=master_url,
    )
