from hashlib import sha256
import pytest

from models.stream import StreamIdentity, build_stream_identity


def test_explicit_stream_id_is_preserved_for_public_use():
    identity = build_stream_identity(
        "https://example/master.m3u8",
        " channel-01 ",
    )

    assert identity.external_stream_id == "channel-01"
    assert identity.storage_id != "channel-01"
    assert len(identity.storage_id) == 24
    assert identity.master_url == "https://example/master.m3u8"


def test_same_external_id_different_url_produces_same_storage_id():
    id1 = build_stream_identity("https://cdn1.example/master.m3u8", "channel-01")
    id2 = build_stream_identity("https://cdn2.example/master.m3u8", "channel-01")

    assert id1.external_stream_id == "channel-01"
    assert id2.external_stream_id == "channel-01"
    assert id1.storage_id == id2.storage_id


def test_different_external_id_same_url_produces_different_storage_id():
    id1 = build_stream_identity("https://cdn.example/master.m3u8", "channel-01")
    id2 = build_stream_identity("https://cdn.example/master.m3u8", "channel-02")

    assert id1.external_stream_id == "channel-01"
    assert id2.external_stream_id == "channel-02"
    assert id1.storage_id != id2.storage_id


def test_omitted_stream_id_makes_external_equal_to_storage():
    url = "https://cdn.example/live/master.m3u8"
    identity = build_stream_identity(url)

    expected_hash = sha256(url.encode("utf-8")).hexdigest()[:24]
    assert identity.external_stream_id == expected_hash
    assert identity.storage_id == expected_hash
    assert len(identity.storage_id) == 24


def test_empty_or_whitespace_stream_id_raises_value_error():
    with pytest.raises(ValueError, match="stream_id must not be empty"):
        build_stream_identity("https://example/master.m3u8", "")

    with pytest.raises(ValueError, match="stream_id must not be empty"):
        build_stream_identity("https://example/master.m3u8", "   ")


def test_stream_id_longer_than_public_contract_is_rejected():
    with pytest.raises(ValueError, match="must not exceed 128"):
        build_stream_identity(
            "https://example/master.m3u8",
            "x" * 129,
        )


def test_storage_hash_matches_legacy_compatibility():
    raw_id = "channel-01"
    identity = build_stream_identity("https://example/master.m3u8", raw_id)

    legacy_hash = sha256(raw_id.encode("utf-8")).hexdigest()[:24]
    assert identity.storage_id == legacy_hash
