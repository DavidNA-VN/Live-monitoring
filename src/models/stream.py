from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class StreamIdentity:
    """
    Stable identity of one monitored live channel/source.

    stream_id should preferably come from the application/business
    layer (channel ID, service ID, etc.).

    When no explicit ID exists yet, a deterministic fingerprint of
    the supplied master URL can be used.
    """

    stream_id: str
    master_url: str


def build_stream_identity(
    master_url: str,
    stream_id: str | None = None,
) -> StreamIdentity:

    if stream_id is not None:
        normalized = stream_id.strip()

        if not normalized:
            raise ValueError(
                "stream_id must not be empty"
            )

        stable_id = sha256(
            normalized.encode("utf-8")
        ).hexdigest()[:24]

    else:
        stable_id = sha256(
            master_url.encode("utf-8")
        ).hexdigest()[:24]

    return StreamIdentity(
        stream_id=stable_id,
        master_url=master_url,
    )