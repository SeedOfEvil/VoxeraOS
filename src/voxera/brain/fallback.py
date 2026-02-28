"""Stable fallback reason enum + classifier for brain fallback transitions."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Stable enum-like reason strings.  Keep alphabetical after UNKNOWN.
# ---------------------------------------------------------------------------
TIMEOUT = "TIMEOUT"
AUTH = "AUTH"
RATE_LIMIT = "RATE_LIMIT"
MALFORMED = "MALFORMED"
NETWORK = "NETWORK"
UNKNOWN = "UNKNOWN"

ALL_REASONS: frozenset[str] = frozenset({TIMEOUT, AUTH, RATE_LIMIT, MALFORMED, NETWORK, UNKNOWN})


def classify_fallback_reason(exc: BaseException) -> str:
    """Map a brain provider exception to a stable fallback reason string.

    Classification is intentionally conservative: ambiguous failures map to
    ``UNKNOWN`` rather than risk a wrong label.
    """
    msg = str(exc).lower()

    # --- timeout ---------------------------------------------------------
    if _is_timeout(exc, msg):
        return TIMEOUT

    # --- rate limit (429) ------------------------------------------------
    if "429" in msg or "rate limit" in msg or "rate_limit" in msg:
        return RATE_LIMIT

    # --- auth (401 / 403) ------------------------------------------------
    if _is_auth(exc, msg):
        return AUTH

    # --- malformed (JSON decode / schema) --------------------------------
    if isinstance(exc, (ValueError,)) and type(exc).__name__ == "JSONDecodeError":
        return MALFORMED
    if _is_malformed(msg):
        return MALFORMED

    # --- network (DNS / connection / reset) ------------------------------
    if _is_network(exc, msg):
        return NETWORK

    return UNKNOWN


# ---- private helpers (keep deterministic, no side-effects) ---------------

_TIMEOUT_KEYWORDS = ("timed out", "timeout", "deadline exceeded")


def _is_timeout(exc: BaseException, msg: str) -> bool:
    import asyncio

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:  # pragma: no cover
        pass
    return any(kw in msg for kw in _TIMEOUT_KEYWORDS)


_AUTH_STATUS_RE = re.compile(r"\b(401|403)\b")


def _is_auth(exc: BaseException, msg: str) -> bool:
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (401, 403)
    except ImportError:  # pragma: no cover
        pass
    if _AUTH_STATUS_RE.search(msg):
        # Avoid false-positive: 429 already handled above.
        return True
    return "unauthorized" in msg or "forbidden" in msg or "auth" in msg


_MALFORMED_KEYWORDS = (
    "json",
    "decode",
    "malformed",
    "invalid schema",
    "non-json",
    "schema_mismatch",
)


def _is_malformed(msg: str) -> bool:
    return any(kw in msg for kw in _MALFORMED_KEYWORDS)


_NETWORK_KEYWORDS = (
    "dns",
    "name resolution",
    "connection refused",
    "connection reset",
    "connect error",
    "network unreachable",
    "no route",
    "broken pipe",
)


def _is_network(exc: BaseException, msg: str) -> bool:
    try:
        import httpx

        if isinstance(exc, httpx.ConnectError):
            return True
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, ConnectionError):
        return True
    return any(kw in msg for kw in _NETWORK_KEYWORDS)
