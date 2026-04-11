"""Panel security / health snapshot helper cluster.

This module owns the narrow cluster that wires the panel composition root
to the health snapshot file: deriving the health-file queue root from the
configured panel queue root (with the VOXERA_HEALTH_PATH isolation escape
hatch), incrementing the panel security counters, reading the panel
security snapshot, and rendering the auth-setup banner shown at the top of
operator pages when ``VOXERA_PANEL_OPERATOR_PASSWORD`` is not configured.
It was extracted from ``panel/app.py`` as the third small, behavior-
preserving step of decomposing that composition root (PR C).

``panel/app.py`` remains the composition root: it still defines the
FastAPI app, registers routes, and owns the shared ``_settings`` /
``_queue_root`` / ``_now_ms`` wrappers, plus the thin
``_health_queue_root`` / ``_panel_security_counter_incr`` /
``_panel_security_snapshot`` / ``_auth_setup_banner`` wrappers that route
modules and ``auth_enforcement`` reach back for. This module is explicit
and pure-ish: every function takes its dependencies as explicit arguments
(the configured queue root, the settings object) so there is no hidden
module-level state, no import of ``panel.app``, and the module is easy to
unit-test in isolation.

Semantics preserved exactly:

* ``health_queue_root`` — when ``VOXERA_HEALTH_PATH`` is unset the panel's
  configured queue root is returned as-is. When it is set **and**
  ``VOXERA_QUEUE_ROOT`` is unset **and** the configured queue root
  resolves to the default repo queue (``$CWD/notes/queue``), ``None`` is
  returned so the caller falls through to the isolated health path. In
  every other case the configured queue root is returned. This matches
  the original in-app implementation byte-for-byte.
* ``panel_security_counter_incr`` — forwards to
  ``voxera.health.increment_health_counter`` with the same arguments.
* ``panel_security_snapshot`` — forwards to
  ``voxera.health.read_health_snapshot`` and returns the ``counters`` sub
  dict (or ``{}`` when absent / the wrong type).
* ``auth_setup_banner`` — returns ``None`` when the operator password is
  set; otherwise returns the same four-key banner dict (title, detail,
  path_hint, commands) used by the operator page templates.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..health import increment_health_counter, read_health_snapshot

__all__ = [
    "auth_setup_banner",
    "health_queue_root",
    "panel_security_counter_incr",
    "panel_security_snapshot",
]


def health_queue_root(queue_root: Path) -> Path | None:
    """Derive the health-file queue root for the panel.

    Mirrors the original ``panel.app._health_queue_root`` semantics
    exactly: production/runtime paths (no ``VOXERA_HEALTH_PATH`` set, or
    an explicit ``VOXERA_QUEUE_ROOT`` override) return the configured
    panel queue root unchanged; only the test-only safety net (when the
    panel would otherwise target the repo default ``$CWD/notes/queue``
    under an isolated ``VOXERA_HEALTH_PATH``) returns ``None`` so the
    caller falls through to the isolated health path.
    """

    isolated_health = os.getenv("VOXERA_HEALTH_PATH", "").strip()
    if not isolated_health:
        return queue_root

    # Keep production/runtime semantics unchanged for explicit queue roots.
    if os.getenv("VOXERA_QUEUE_ROOT", "").strip():
        return queue_root

    configured_root = queue_root.expanduser().resolve()
    repo_operator_root = (Path.cwd() / "notes" / "queue").resolve()
    # Test-only safety net: when panel would target the repo default queue root,
    # route health writes through VOXERA_HEALTH_PATH instead.
    if configured_root == repo_operator_root:
        return None
    return queue_root


def panel_security_counter_incr(
    queue_root: Path | None,
    key: str,
    *,
    last_error: str | None = None,
) -> None:
    """Increment a panel security counter in the health snapshot.

    Thin forwarder to ``voxera.health.increment_health_counter`` that
    matches the original ``panel.app._panel_security_counter_incr``
    signature and behavior exactly (``queue_root`` already resolved via
    ``health_queue_root`` by the caller).
    """

    increment_health_counter(queue_root, key, last_error=last_error)


def panel_security_snapshot(queue_root: Path | None) -> dict[str, Any]:
    """Read the panel security counters sub-dict from the health snapshot.

    Mirrors the original ``panel.app._panel_security_snapshot`` exactly:
    reads the full health snapshot and returns its ``counters`` sub-dict
    when present and dict-typed, otherwise returns an empty dict.
    """

    payload = read_health_snapshot(queue_root)
    counters = payload.get("counters")
    return counters if isinstance(counters, dict) else {}


def auth_setup_banner(settings: Any) -> dict[str, str] | None:
    """Return the auth-setup banner when no operator password is configured.

    Mirrors the original ``panel.app._auth_setup_banner`` exactly: when
    ``settings.panel_operator_password`` is a non-empty string, returns
    ``None`` (no banner); otherwise returns the same four-key dict used
    by the operator page templates (``title``, ``detail``, ``path_hint``,
    ``commands``).
    """

    if settings.panel_operator_password not in {None, ""}:
        return None
    config_path_hint = str(settings.config_path.expanduser())
    return {
        "title": "Setup required: panel operator password is not configured.",
        "detail": (
            "Mutation routes require Basic auth. Set VOXERA_PANEL_OPERATOR_PASSWORD in your "
            "user service environment and restart panel + daemon. If VOXERA_LOAD_DOTENV=1, "
            ".env may override file settings."
        ),
        "path_hint": f"Config file: {config_path_hint}",
        "commands": (
            "systemctl --user edit voxera-panel.service\n"
            "# add [Service] Environment=VOXERA_PANEL_OPERATOR_PASSWORD=<set-a-strong-password>\n"
            "systemctl --user daemon-reload\n"
            "systemctl --user restart voxera-panel.service voxera-daemon.service"
        ),
    }
