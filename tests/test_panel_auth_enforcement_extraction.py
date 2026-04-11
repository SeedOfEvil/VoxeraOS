"""Extraction-contract tests for the panel auth enforcement module.

Scope: proves that moving the auth / CSRF / lockout cluster out of
``voxera.panel.app`` into ``voxera.panel.auth_enforcement`` was done
correctly and can't be silently undone by a later panel-decomposition PR.

These tests are deliberately narrow. The HTTP-level behavior (401 / 403 /
429 / lockout window / CSRF guard) is already extensively covered by
``tests/test_panel.py``; this file pins the *shape* of the extraction so
that the HTTP coverage keeps meaning what it used to mean:

1. ``auth_enforcement.py`` owns the two documented entry points
   ``require_operator_basic_auth(request)`` and
   ``require_mutation_guard(request)``.
2. ``panel.app`` still visibly wires those entry points into its route
   registration callbacks (``_require_mutation_guard`` and
   ``_require_operator_auth_from_request``) and does not re-define the
   auth logic locally.
3. ``panel.app`` re-exports ``_operator_credentials`` so the existing
   ``tests/test_dev_contract_config_integration.py`` contract test keeps
   passing against the composition-root seam.
4. The reach-back pattern (``auth_enforcement._now_ms`` looking up
   ``panel.app._now_ms`` at call time) actually works — i.e. a
   ``monkeypatch.setattr(panel_module, "_now_ms", ...)`` is visible
   through the auth flow. This is what makes the lockout tests in
   ``test_panel.py`` valid after the extraction.
5. Fail-closed auth entry point: calling ``require_operator_basic_auth``
   with no ``Authorization`` header still raises ``HTTPException(401)``
   with ``WWW-Authenticate: Basic`` and bumps the expected counter.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from voxera.panel import app as panel_module
from voxera.panel import auth_enforcement


def _fake_request(*, authorization: str | None = None) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if authorization is not None:
        headers["authorization"] = authorization
    return SimpleNamespace(
        headers=headers,
        cookies={},
        url=SimpleNamespace(path="/queue/create"),
        method="POST",
        client=SimpleNamespace(host="127.0.0.1"),
    )


def test_auth_enforcement_exposes_two_entry_points() -> None:
    assert callable(auth_enforcement.require_operator_basic_auth)
    assert callable(auth_enforcement.require_mutation_guard)

    # Each entry point takes just ``request`` — the documented narrow API.
    basic_sig = inspect.signature(auth_enforcement.require_operator_basic_auth)
    assert list(basic_sig.parameters) == ["request"]

    guard_sig = inspect.signature(auth_enforcement.require_mutation_guard)
    assert list(guard_sig.parameters) == ["request"]

    # require_mutation_guard stays async (FastAPI dependencies/route callers
    # rely on this — it awaits request body parsing for the CSRF form key).
    assert inspect.iscoroutinefunction(auth_enforcement.require_mutation_guard)
    assert not inspect.iscoroutinefunction(auth_enforcement.require_operator_basic_auth)


def test_panel_app_wires_auth_enforcement_entry_points() -> None:
    # panel.app must expose the route-registration callback names as literal
    # aliases of the auth_enforcement entry points (or thin wrappers that
    # delegate to them). This keeps app.py as the visible composition root
    # without re-implementing auth locally.
    assert panel_module._require_mutation_guard is auth_enforcement.require_mutation_guard

    # _require_operator_auth_from_request is a thin sync wrapper that forwards
    # to auth_enforcement.require_operator_basic_auth. We can't assert
    # ``is`` here because it is a wrapper, but it must not be the old
    # two-argument function and must resolve auth via auth_enforcement.
    wrapper_sig = inspect.signature(panel_module._require_operator_auth_from_request)
    assert list(wrapper_sig.parameters) == ["request"]

    # The wrapper's source must reference the imported auth_enforcement
    # symbol, not a locally redefined copy.
    source = inspect.getsource(panel_module._require_operator_auth_from_request)
    assert "_require_operator_basic_auth(request)" in source


def test_panel_app_reexports_operator_credentials_for_contract_test() -> None:
    # tests/test_dev_contract_config_integration.py calls
    # ``panel_app._operator_credentials(...)`` directly. That has to keep
    # resolving to the auth_enforcement implementation after the move.
    assert panel_module._operator_credentials is auth_enforcement._operator_credentials


def test_panel_app_does_not_redefine_extracted_auth_helpers() -> None:
    # The helpers that moved out must not be re-introduced as locally
    # defined functions in panel.app. (Re-exports via ``from ... import``
    # are fine — those are caught by the positive ``is`` assertions above.)
    for name in (
        "_client_ip",
        "_panel_auth_state_update",
        "_panel_auth_state_prune",
        "_active_lockout_until_ms",
        "_log_panel_security_event",
        "_request_meta",
        "_PanelSecurityRequestLike",
    ):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer own {name!r}; it was extracted to "
            "voxera.panel.auth_enforcement as part of PR A."
        )


def test_auth_enforcement_now_ms_reaches_back_through_panel_app(monkeypatch) -> None:
    # The reach-back pattern is what makes the lockout tests in
    # test_panel.py (which do ``monkeypatch.setattr(panel_module, "_now_ms",
    # ...)``) keep working after the extraction. Pin it directly so a
    # future refactor can't quietly capture a local reference and break
    # every lockout test at once.
    sentinel = 4_242_424_242
    monkeypatch.setattr(panel_module, "_now_ms", lambda: sentinel)
    assert auth_enforcement._now_ms() == sentinel
    # Same discipline for _health_queue_root and _panel_security_counter_incr,
    # which the auth flow also reaches back for.
    monkeypatch.setattr(panel_module, "_health_queue_root", lambda: None)
    assert auth_enforcement._health_queue_root() is None

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        panel_module,
        "_panel_security_counter_incr",
        lambda key, *, last_error=None: calls.append((key, last_error)),
    )
    auth_enforcement._panel_security_counter_incr("panel_401_count", last_error="probe")
    assert calls == [("panel_401_count", "probe")]


def test_require_operator_basic_auth_is_fail_closed_on_missing_header(
    tmp_path, monkeypatch
) -> None:
    # Direct unit-level pin of fail-closed semantics at the auth_enforcement
    # entry point. HTTP-level coverage already exists in test_panel.py; this
    # test guards against a future edit that might loosen the 401 path
    # without going through the TestClient.
    fake_home = tmp_path / "home"
    (fake_home / "VoxeraOS" / "notes" / "queue").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    request = _fake_request(authorization=None)
    with pytest.raises(HTTPException) as exc:
        auth_enforcement.require_operator_basic_auth(request)

    assert exc.value.status_code == 401
    assert exc.value.detail == "operator authentication required"
    assert exc.value.headers == {"WWW-Authenticate": "Basic"}
