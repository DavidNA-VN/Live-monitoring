from datetime import datetime, timedelta, timezone
import pytest

from app.command_guardrails import CommandGuardrails


def test_command_guardrails_default_payload_size():
    guardrails = CommandGuardrails(max_payload_bytes=100)

    # Within limit
    valid, size = guardrails.check_payload_size("a" * 100)
    assert valid is True
    assert size == 100

    # Over limit
    valid, size = guardrails.check_payload_size("a" * 101)
    assert valid is False
    assert size == 101


def test_command_guardrails_utf8_multibyte_sizing():
    guardrails = CommandGuardrails(max_payload_bytes=10)
    # Character '€' is 3 bytes in UTF-8
    valid, size = guardrails.check_payload_size("€€€")  # 9 bytes
    assert valid is True
    assert size == 9

    valid, size = guardrails.check_payload_size("€€€€")  # 12 bytes
    assert valid is False
    assert size == 12


def test_command_guardrails_bytes_input():
    guardrails = CommandGuardrails(max_payload_bytes=5)
    valid, size = guardrails.check_payload_size(b"12345")
    assert valid is True
    assert size == 5

    valid, size = guardrails.check_payload_size(b"123456")
    assert valid is False
    assert size == 6


def test_command_age_disabled_by_default():
    guardrails = CommandGuardrails(max_command_age_seconds=None)
    requested = datetime.now(timezone.utc) - timedelta(days=365)
    valid, error = guardrails.check_command_age(requested)
    assert valid is True
    assert error is None


def test_command_age_enabled_rejects_stale():
    guardrails = CommandGuardrails(max_command_age_seconds=60.0)
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)

    # 30s old -> valid
    valid, error = guardrails.check_command_age(now - timedelta(seconds=30), now=now)
    assert valid is True
    assert error is None

    # 61s old -> stale
    valid, error = guardrails.check_command_age(now - timedelta(seconds=61), now=now)
    assert valid is False
    assert error == "STALE_COMMAND"


def test_command_age_reclaimed_exemption():
    guardrails = CommandGuardrails(max_command_age_seconds=60.0)
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    old = now - timedelta(seconds=300)

    # When marked is_reclaimed=True, do not reject
    valid, error = guardrails.check_command_age(old, now=now, is_reclaimed=True)
    assert valid is True
    assert error is None


def test_command_future_timestamp_tolerance():
    guardrails = CommandGuardrails(max_command_age_seconds=60.0, future_tolerance_seconds=10.0)
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)

    # 5s in future -> allowed (within clock skew tolerance)
    valid, error = guardrails.check_command_age(now + timedelta(seconds=5), now=now)
    assert valid is True

    # 15s in future -> rejected
    valid, error = guardrails.check_command_age(now + timedelta(seconds=15), now=now)
    assert valid is False
    assert error == "FUTURE_COMMAND"
