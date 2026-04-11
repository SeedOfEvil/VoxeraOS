"""Panel auth enforcement, CSRF mutation guard, and lockout mechanics.

This module owns the operator Basic-auth guard, the CSRF mutation guard, and
the per-IP failure/lockout bookkeeping that protect the panel's mutation
routes. It was extracted from ``panel/app.py`` as the first small, behavior-
preserving step of decomposing that composition root.

``panel/app.py`` remains the composition root: it still defines the FastAPI
app, registers routes, and owns the shared security wrappers (``_settings``,
``_now_ms``, ``_health_queue_root``, ``_panel_security_counter_incr``) that
other panel clusters also depend on. This module reaches back to those
wrappers via a lazy import so that tests which ``monkeypatch`` them on the
``panel.app`` module (e.g. ``_now_ms`` for the lockout tests) continue to drive
the auth flow exactly as before.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from fastapi import HTTPException, Request, status

from ..audit import log
from ..health import update_health_snapshot
from .auth_state_store import (
    PANEL_AUTH_LOCKOUT_S,
    PANEL_AUTH_WINDOW_S,
)
from .auth_state_store import active_lockout_until_ms as _stored_active_lockout_until_ms
from .auth_state_store import apply_panel_auth_state_prune as _apply_panel_auth_state_prune
from .auth_state_store import apply_panel_auth_state_update as _apply_panel_auth_state_update
from .auth_state_store import auth_failure_snapshot as _auth_failure_snapshot
from .helpers import request_value as _request_value

# CSRF constants. These are duplicated from ``panel/app.py`` (which keeps its
# own copies to pass as route-registration callback parameters) so that this
# module has no top-level dependency on ``panel.app``. If either value is ever
# renamed, update both sites together.
CSRF_COOKIE = "voxera_panel_csrf"
CSRF_FORM_KEY = "csrf_token"


class _RequestUrlLike(Protocol):
    @property
    def path(self) -> str: ...


class _RequestClientLike(Protocol):
    @property
    def host(self) -> str: ...


class _PanelSecurityRequestLike(Protocol):
    @property
    def url(self) -> _RequestUrlLike: ...

    @property
    def method(self) -> str: ...

    @property
    def client(self) -> _RequestClientLike | None: ...


def _panel_app() -> ModuleType:
    """Return the ``voxera.panel.app`` module.

    Lazy import to avoid a circular import at module load time. After the
    first call Python's import cache makes subsequent lookups effectively
    free. Going through the module attribute (rather than capturing a direct
    reference) is what makes ``monkeypatch.setattr(panel_module, "_now_ms",
    ...)`` take effect inside this module's auth flow.
    """

    from . import app as panel_app_module

    return panel_app_module


def _settings() -> Any:
    return _panel_app()._settings()


def _now_ms() -> int:
    return int(_panel_app()._now_ms())


def _health_queue_root() -> Path | None:
    return _panel_app()._health_queue_root()


def _panel_security_counter_incr(key: str, *, last_error: str | None = None) -> None:
    _panel_app()._panel_security_counter_incr(key, last_error=last_error)


def _client_ip(request: Request) -> str:
    trust_proxy = os.getenv("VOXERA_PANEL_TRUST_PROXY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _panel_auth_state_update(
    queue_root: Path | None,
    *,
    ip: str,
    now_ms: int,
    auth_success: bool,
) -> dict[str, Any]:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        return _apply_panel_auth_state_update(
            payload,
            ip=ip,
            now_ms=now_ms,
            auth_success=auth_success,
        )

    return update_health_snapshot(queue_root, _apply)


def _panel_auth_state_prune(queue_root: Path | None, *, now_ms: int) -> dict[str, Any]:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        return _apply_panel_auth_state_prune(payload, now_ms=now_ms)

    return update_health_snapshot(queue_root, _apply)


def _active_lockout_until_ms(*, queue_root: Path | None, ip: str, now_ms: int) -> int | None:
    payload = _panel_auth_state_prune(queue_root, now_ms=now_ms)
    panel_auth_raw = payload.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    return _stored_active_lockout_until_ms(panel_auth, ip=ip, now_ms=now_ms)


def _request_meta(request: _PanelSecurityRequestLike) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "method": request.method,
        "remote": (request.client.host if request.client else "unknown"),
    }


def _log_panel_security_event(
    event: str,
    *,
    request: _PanelSecurityRequestLike,
    reason: str,
    status_code: int,
) -> None:
    meta = _request_meta(request)
    log(
        {
            "event": event,
            "ts_ms": int(time.time() * 1000),
            "path": meta["path"],
            "method": meta["method"],
            "remote": meta["remote"],
            "reason": reason,
            "status_code": status_code,
        }
    )


def _operator_credentials(request: _PanelSecurityRequestLike) -> tuple[str, str]:
    settings = _settings()
    user = settings.panel_operator_user
    password = settings.panel_operator_password
    if not password:
        _panel_security_counter_incr("panel_401_count", last_error="operator password missing")
        _log_panel_security_event(
            "panel_operator_config_error",
            request=request,
            reason="operator_password_missing",
            status_code=503,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VOXERA_PANEL_OPERATOR_PASSWORD must be set",
        )
    return user, password


def require_operator_basic_auth(request: Request) -> None:
    """Enforce operator Basic auth on the given request.

    Fail-closed: any missing/invalid credential path raises ``HTTPException``
    (401, 429, or 503) after updating the per-IP failure/lockout state and
    bumping the corresponding security counters.
    """

    authorization = request.headers.get("authorization")
    user, password = _operator_credentials(request)
    now_ms = _now_ms()
    ip = _client_ip(request)
    lockout_until_ms = _active_lockout_until_ms(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms
    )
    if lockout_until_ms is not None:
        _panel_security_counter_incr("panel_429_count")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many authentication attempts",
            headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
        )

    if not authorization:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr("panel_401_count", last_error="missing authorization")
        _log_panel_security_event(
            "panel_auth_missing", request=request, reason="missing_authorization", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="operator authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authentication scheme"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_scheme", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication scheme",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authorization header"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_header", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc
    got_user, _, got_password = decoded.partition(":")
    if not (
        secrets.compare_digest(got_user, user) and secrets.compare_digest(got_password, password)
    ):
        payload = _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        attempt_count, lockout_until_ms = _auth_failure_snapshot(panel_auth, ip=ip)
        if now_ms < lockout_until_ms:
            _panel_security_counter_incr("panel_429_count")
            log(
                {
                    "event": "panel_auth_lockout",
                    "ts_ms": now_ms,
                    "ip": ip,
                    "attempt_count": attempt_count,
                    "window_s": PANEL_AUTH_WINDOW_S,
                    "lockout_s": PANEL_AUTH_LOCKOUT_S,
                }
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many authentication attempts",
                headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
            )

        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid operator credentials"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_credentials", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid operator credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    _panel_auth_state_update(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=True
    )


async def require_mutation_guard(request: Request) -> None:
    """Enforce operator auth + CSRF on a mutation request.

    Fail-closed: when CSRF is enabled (the default) both a non-empty cookie
    token and a non-empty request token (header ``x-csrf-token`` or
    ``csrf_token`` form/query value) must be present and must match via
    ``secrets.compare_digest``.
    """

    require_operator_basic_auth(request)
    if not _settings().panel_csrf_enabled:
        _panel_security_counter_incr("panel_mutation_allowed")
        _log_panel_security_event(
            "panel_mutation_allowed",
            request=request,
            reason="auth_valid_csrf_disabled",
            status_code=200,
        )
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    request_token = (request.headers.get("x-csrf-token") or "").strip() or (
        await _request_value(request, CSRF_FORM_KEY, "")
    ).strip()
    if not cookie_token or not request_token:
        _panel_security_counter_incr("panel_403_count", last_error="csrf token missing")
        _panel_security_counter_incr("panel_csrf_missing")
        _log_panel_security_event(
            "panel_csrf_missing", request=request, reason="csrf_token_missing", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    if not secrets.compare_digest(cookie_token, request_token):
        _panel_security_counter_incr("panel_403_count", last_error="csrf token mismatch")
        _panel_security_counter_incr("panel_csrf_invalid")
        _log_panel_security_event(
            "panel_csrf_invalid", request=request, reason="csrf_token_mismatch", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    _panel_security_counter_incr("panel_mutation_allowed")
    _log_panel_security_event(
        "panel_mutation_allowed",
        request=request,
        reason="auth_and_csrf_valid",
        status_code=200,
    )
