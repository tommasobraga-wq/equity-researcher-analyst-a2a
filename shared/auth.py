"""HMAC-SHA256 request/response signing for the A2A protocol.

Single shared secret (A2A_SHARED_SECRET) known to the orchestrator and all
5 agents — appropriate for a closed pipeline where the only requirement is
"only the orchestrator may invoke the agents, and the orchestrator must
trust the identity of whoever answers." Both directions are signed:
orchestrator -> agent requests, and agent -> orchestrator responses.
"""
import hmac
import os
import time
from hashlib import sha256

_MAX_SKEW_SECONDS = 300


def enforce_secret_policy() -> None:
    """Fails fast at process startup if A2A_AUTH_REQUIRED=true but no shared
    secret is configured — makes "running without auth" an explicit opt-in
    (the default) rather than a silent gap discovered later in production."""
    required = os.getenv("A2A_AUTH_REQUIRED", "false").lower() == "true"
    if required and not os.getenv("A2A_SHARED_SECRET"):
        raise RuntimeError(
            "A2A_AUTH_REQUIRED=true but A2A_SHARED_SECRET is not set. "
            "Set A2A_SHARED_SECRET or unset A2A_AUTH_REQUIRED."
        )


def sign(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, sha256)
    return mac.hexdigest()


def verify(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    """Checks both the signature (constant-time) and timestamp freshness
    (replay protection — a captured, validly-signed request/response can't
    be resent after `_MAX_SKEW_SECONDS`)."""
    try:
        age = abs(time.time() - float(timestamp))
    except (TypeError, ValueError):
        return False
    if age > _MAX_SKEW_SECONDS:
        return False

    expected = sign(secret, body, timestamp)
    return hmac.compare_digest(expected, signature)
