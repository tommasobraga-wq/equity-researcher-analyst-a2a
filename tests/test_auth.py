"""Unit tests for shared/auth.py — HMAC signing, verification, replay protection."""
import time

import pytest

from shared.auth import enforce_secret_policy, sign, verify


def test_sign_verify_roundtrip():
    secret = "s3cr3t"
    body = b'{"hello":"world"}'
    ts = str(time.time())
    sig = sign(secret, body, ts)
    assert verify(secret, body, ts, sig)


def test_verify_rejects_wrong_secret():
    body = b"payload"
    ts = str(time.time())
    sig = sign("secret-a", body, ts)
    assert not verify("secret-b", body, ts, sig)


def test_verify_rejects_tampered_body():
    secret = "s3cr3t"
    ts = str(time.time())
    sig = sign(secret, b"original", ts)
    assert not verify(secret, b"tampered", ts, sig)


def test_verify_rejects_stale_timestamp():
    secret = "s3cr3t"
    body = b"payload"
    ts = str(time.time() - 1000)  # older than _MAX_SKEW_SECONDS (300)
    sig = sign(secret, body, ts)
    assert not verify(secret, body, ts, sig)


def test_verify_rejects_malformed_timestamp():
    secret = "s3cr3t"
    body = b"payload"
    assert not verify(secret, body, "not-a-timestamp", "deadbeef")


def test_enforce_secret_policy_raises_when_required_and_missing(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_REQUIRED", "true")
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        enforce_secret_policy()


def test_enforce_secret_policy_passes_when_required_and_present(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_REQUIRED", "true")
    monkeypatch.setenv("A2A_SHARED_SECRET", "x")
    enforce_secret_policy()  # must not raise


def test_enforce_secret_policy_passes_when_not_required(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_REQUIRED", "false")
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    enforce_secret_policy()  # must not raise
