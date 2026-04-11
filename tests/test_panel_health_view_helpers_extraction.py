"""Extraction-contract tests for the panel health-view / formatting helper module.

Scope: proves that moving the health-view and formatting helper cluster
out of ``voxera.panel.app`` into ``voxera.panel.health_view_helpers`` was
done correctly and can't be silently undone by a later panel-decomposition
PR.

These tests are deliberately narrow. The HTTP-level rendering of the home
page's Daemon Health widget and Performance Stats tab is already covered
by ``tests/test_panel.py::test_home_renders_daemon_health_widget_*`` and
``tests/test_panel.py::test_home_renders_performance_stats_tab`` /
``test_home_performance_history_missing_shows_dash``; this file pins the
*shape* of the extraction so the existing HTTP coverage keeps meaning
what it used to mean:

1. ``health_view_helpers.py`` owns the two documented entry points
   ``daemon_health_view(health)`` and
   ``performance_stats_view(queue, health)``, plus the five narrow
   formatting helpers that only exist to support those two views
   (``format_ts``, ``format_ts_seconds``, ``format_age``,
   ``history_value``, ``history_pair``).
2. ``panel.app`` still visibly wires those entry points into its thin
   wrapper callbacks (``_daemon_health_view``, ``_performance_stats_view``,
   ``_format_ts``) and each wrapper forwards to the extracted helper via
   its ``_*_impl`` alias.
3. ``panel.app`` no longer defines the formatting / shaping helper bodies
   inline: ``_format_ts_seconds``, ``_format_age``, ``_history_value``,
   ``_history_pair`` are fully removed from ``panel.app`` (``hasattr``
   check), and ``panel.app`` no longer imports ``datetime`` /
   ``build_health_semantic_sections`` / ``coerce_int as _coerce_int``.
4. ``health_view_helpers.py`` does NOT reach back into ``panel.app`` via
   any import (AST-level check rules out ``from . import app`` /
   ``from .app import ...`` / ``from .routes_* import ...``), pinning
   the explicit-args architecture invariant matching PR B's
   ``queue_mutation_bridge``, PR C's ``security_health_helpers``, and
   PR D's ``job_detail_sections``.
5. ``daemon_health_view`` and ``performance_stats_view`` preserve the
   documented payload shape byte-for-byte: the same top-level key sets,
   the same field names under ``last_brain_fallback`` /
   ``last_startup_recovery`` / ``last_shutdown``, the same nested
   ``queue_counts`` / ``current_state`` / ``recent_history`` /
   ``historical_counters`` composition. A payload key-set shape lock
   on each view freezes the key set so a later PR can't silently add,
   rename, or drop a payload key.
6. Formatting semantics are pinned across the edge cases that
   ``home.html`` depends on: em-dash fallback for ``None`` /
   non-positive timestamps, compact minute-second age labels,
   history-line ``"-"`` fallback for empty values, and the pair
   formatter's double-dash guard.
"""

from __future__ import annotations

import ast
import inspect

from voxera.panel import app as panel_module
from voxera.panel import health_view_helpers

_EXPECTED_DAEMON_HEALTH_VIEW_KEYS: frozenset[str] = frozenset(
    {
        "lock_status",
        "lock_pid",
        "lock_stale_age_s",
        "lock_stale_age_label",
        "last_brain_fallback",
        "last_startup_recovery",
        "last_shutdown",
        "daemon_state",
    }
)

_EXPECTED_PERFORMANCE_STATS_VIEW_KEYS: frozenset[str] = frozenset(
    {
        "queue_counts",
        "current_state",
        "recent_history",
        "historical_counters",
    }
)

_EXPECTED_HISTORICAL_COUNTER_KEYS: frozenset[str] = frozenset(
    {
        "panel_auth_invalid",
        "panel_401_count",
        "panel_403_count",
        "panel_429_count",
        "panel_csrf_missing",
        "panel_csrf_invalid",
        "panel_mutation_allowed",
        "brain_fallback_count",
        "brain_fallback_reason_timeout",
        "brain_fallback_reason_auth",
        "brain_fallback_reason_rate_limit",
        "brain_fallback_reason_malformed",
        "brain_fallback_reason_network",
        "brain_fallback_reason_unknown",
    }
)


def test_health_view_helpers_exposes_documented_entry_points() -> None:
    for name in (
        "daemon_health_view",
        "performance_stats_view",
        "format_ts",
        "format_ts_seconds",
        "format_age",
        "history_value",
        "history_pair",
    ):
        assert hasattr(health_view_helpers, name), (
            f"health_view_helpers must expose {name!r} as a documented entry point."
        )
        assert callable(getattr(health_view_helpers, name))

    daemon_sig = inspect.signature(health_view_helpers.daemon_health_view)
    assert list(daemon_sig.parameters) == ["health"]

    perf_sig = inspect.signature(health_view_helpers.performance_stats_view)
    assert list(perf_sig.parameters) == ["queue", "health"]

    fmt_ts_sig = inspect.signature(health_view_helpers.format_ts)
    assert list(fmt_ts_sig.parameters) == ["ts_ms"]

    fmt_ts_s_sig = inspect.signature(health_view_helpers.format_ts_seconds)
    assert list(fmt_ts_s_sig.parameters) == ["ts_s"]

    fmt_age_sig = inspect.signature(health_view_helpers.format_age)
    assert list(fmt_age_sig.parameters) == ["age_s"]

    history_value_sig = inspect.signature(health_view_helpers.history_value)
    assert list(history_value_sig.parameters) == ["value"]

    history_pair_sig = inspect.signature(health_view_helpers.history_pair)
    assert list(history_pair_sig.parameters) == ["value", "ts_label"]


def test_panel_app_wires_health_view_helper_entry_points() -> None:
    # panel.app must expose its thin wrapper callbacks and each wrapper
    # must forward to the extracted helper function — not re-implement
    # the logic locally.
    for name in ("_daemon_health_view", "_performance_stats_view", "_format_ts"):
        assert callable(getattr(panel_module, name)), f"panel.app must still expose {name}"

    assert "_daemon_health_view_impl(" in inspect.getsource(panel_module._daemon_health_view)
    assert "_performance_stats_view_impl(" in inspect.getsource(
        panel_module._performance_stats_view
    )
    assert "_format_ts_impl(" in inspect.getsource(panel_module._format_ts)


def test_panel_app_no_longer_defines_extracted_private_helpers() -> None:
    # After extraction these private helper bodies must live in
    # health_view_helpers and panel.app must not re-define them.
    for name in (
        "_format_ts_seconds",
        "_format_age",
        "_history_value",
        "_history_pair",
    ):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer define {name}; it now lives behind "
            "voxera.panel.health_view_helpers."
        )


def test_panel_app_no_longer_imports_health_view_primitives_directly() -> None:
    # After extraction the helper module is the only panel-side caller of
    # ``voxera.health_semantics.build_health_semantic_sections`` and is
    # the only panel-side importer of ``datetime`` used for the
    # formatters. Pin that ``panel.app`` does not re-import either so a
    # later PR cannot silently reintroduce a local inline implementation
    # bypassing the extraction.
    assert not hasattr(panel_module, "build_health_semantic_sections"), (
        "panel.app should no longer import build_health_semantic_sections directly; "
        "it now lives behind voxera.panel.health_view_helpers.performance_stats_view."
    )
    assert not hasattr(panel_module, "datetime"), (
        "panel.app should no longer import datetime directly; "
        "the timestamp formatters now live behind voxera.panel.health_view_helpers."
    )


def test_panel_app_wrapper_source_no_longer_contains_extracted_shaping_body() -> None:
    # The performance-stats wrapper body in panel.app used to contain the
    # inline literal counter keys (e.g. ``brain_fallback_reason_timeout``)
    # and the ``historical_counters`` shaping. After extraction the
    # wrapper must visibly delegate; it must not carry those literals.
    wrapper_source = inspect.getsource(panel_module._performance_stats_view)
    assert "brain_fallback_reason_timeout" not in wrapper_source
    assert "historical_counters" not in wrapper_source

    daemon_wrapper_source = inspect.getsource(panel_module._daemon_health_view)
    assert "last_brain_fallback" not in daemon_wrapper_source
    assert "lock_stale_age_label" not in daemon_wrapper_source


def test_health_view_helpers_does_not_reach_back_into_panel_app() -> None:
    # Architecture invariant: like PR B/C/D, this module is pure —
    # every input is explicit. A future PR that sneaks in a
    # ``from . import app`` would quietly reintroduce a circular
    # dependency and hide state. Catch it here via AST so docstrings /
    # comments that mention "panel.app" don't false-positive.
    tree = ast.parse(inspect.getsource(health_view_helpers))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 1 and mod == "app":
                imported_modules.add(".app")
            if node.level >= 1 and mod.startswith("routes_"):
                imported_modules.add(f".{mod}")
            if mod == "voxera.panel.app":
                imported_modules.add("voxera.panel.app")
            if mod.startswith("voxera.panel.routes_"):
                imported_modules.add(mod)
    assert ".app" not in imported_modules
    assert "voxera.panel.app" not in imported_modules
    assert not any(m.startswith(".routes_") for m in imported_modules)
    assert not any(m.startswith("voxera.panel.routes_") for m in imported_modules)


def test_format_ts_em_dash_fallback_for_none_and_non_positive() -> None:
    assert health_view_helpers.format_ts(None) == "—"
    assert health_view_helpers.format_ts(0) == "—"
    assert health_view_helpers.format_ts(-1) == "—"
    assert health_view_helpers.format_ts(1700000000000) == "2023-11-14 22:13:20 UTC"


def test_format_ts_seconds_em_dash_fallback_and_formatting() -> None:
    assert health_view_helpers.format_ts_seconds(None) == "—"
    assert health_view_helpers.format_ts_seconds(0.0) == "—"
    assert health_view_helpers.format_ts_seconds(-0.5) == "—"
    assert health_view_helpers.format_ts_seconds(1700000000.0) == "2023-11-14 22:13:20 UTC"


def test_format_age_em_dash_seconds_minutes_and_minute_second_combo() -> None:
    assert health_view_helpers.format_age(None) == "—"
    assert health_view_helpers.format_age(-5) == "—"
    assert health_view_helpers.format_age(0) == "0s"
    assert health_view_helpers.format_age(45) == "45s"
    assert health_view_helpers.format_age(60) == "1m"
    assert health_view_helpers.format_age(125) == "2m 5s"
    assert health_view_helpers.format_age(3600) == "60m"


def test_history_value_returns_dash_for_empty_and_none() -> None:
    assert health_view_helpers.history_value(None) == "-"
    assert health_view_helpers.history_value("") == "-"
    assert health_view_helpers.history_value("   ") == "-"
    assert health_view_helpers.history_value("boom") == "boom"
    assert health_view_helpers.history_value(42) == "42"


def test_history_pair_dash_guard_and_formatting() -> None:
    # Both empty → plain "-"
    assert health_view_helpers.history_pair(None, "-") == "-"
    assert health_view_helpers.history_pair("", "—") == "-"
    # Value only → "val @ -"
    assert health_view_helpers.history_pair("boom", "-") == "boom @ -"
    # Ts only → "- @ ts"
    assert health_view_helpers.history_pair("", "2023-11-14 22:13:20 UTC") == (
        "- @ 2023-11-14 22:13:20 UTC"
    )
    # Both present
    assert health_view_helpers.history_pair("boom", "2023-11-14 22:13:20 UTC") == (
        "boom @ 2023-11-14 22:13:20 UTC"
    )


def test_daemon_health_view_empty_returns_clear_defaults() -> None:
    view = health_view_helpers.daemon_health_view({})
    assert view["lock_status"] == "clear"
    assert view["lock_pid"] is None
    assert view["lock_stale_age_s"] is None
    assert view["lock_stale_age_label"] == "—"
    assert view["last_brain_fallback"]["present"] is False
    assert view["last_brain_fallback"]["ts"] == "—"
    assert view["last_startup_recovery"]["present"] is False
    assert view["last_startup_recovery"]["job_count"] == 0
    assert view["last_startup_recovery"]["orphan_count"] == 0
    assert view["last_shutdown"]["present"] is False
    assert view["last_shutdown"]["outcome"] == "unknown"
    assert view["daemon_state"] == "healthy"


def test_daemon_health_view_populated_preserves_semantics() -> None:
    view = health_view_helpers.daemon_health_view(
        {
            "lock_status": {"status": "stale", "pid": 4321, "stale_age_s": 125},
            "last_fallback_to": "fallback",
            "last_fallback_reason": "RATE_LIMIT",
            "last_fallback_ts_ms": 1700000000000,
            "last_startup_recovery_counts": {
                "jobs_failed": 2,
                "orphan_approvals_quarantined": 1,
                "orphan_state_files_quarantined": 3,
            },
            "last_startup_recovery_ts": 1700000001000,
            "last_shutdown_outcome": "failed_shutdown",
            "last_shutdown_ts": 1700000002.0,
            "last_shutdown_reason": "KeyboardInterrupt",
            "last_shutdown_job": "job-5.json",
            "daemon_state": "degraded",
        }
    )
    assert view["lock_status"] == "stale"
    assert view["lock_pid"] == 4321
    assert view["lock_stale_age_s"] == 125
    assert view["lock_stale_age_label"] == "2m 5s"
    assert view["last_brain_fallback"] == {
        "present": True,
        "tier": "fallback",
        "reason": "RATE_LIMIT",
        "ts": "2023-11-14 22:13:20 UTC",
    }
    assert view["last_startup_recovery"]["job_count"] == 2
    # orphan_approvals_quarantined (1) + orphan_state_files_quarantined (3) == 4
    assert view["last_startup_recovery"]["orphan_count"] == 4
    assert view["last_startup_recovery"]["present"] is True
    assert view["last_shutdown"]["outcome"] == "failed_shutdown"
    assert view["last_shutdown"]["reason"] == "KeyboardInterrupt"
    assert view["last_shutdown"]["job"] == "job-5.json"
    assert view["daemon_state"] == "degraded"


def test_daemon_health_view_lock_state_fallback_when_no_lock_status_dict() -> None:
    # When lock_status is not a dict, the lock_state fallback rules apply.
    view_active = health_view_helpers.daemon_health_view({"lock_state": "active"})
    assert view_active["lock_status"] == "held"

    view_stale = health_view_helpers.daemon_health_view({"lock_state": "reclaimed"})
    assert view_stale["lock_status"] == "stale"

    view_other = health_view_helpers.daemon_health_view({"lock_state": "nothing"})
    assert view_other["lock_status"] == "clear"


def test_performance_stats_view_key_set_shape_lock() -> None:
    view = health_view_helpers.performance_stats_view({}, {})
    assert set(view.keys()) == set(_EXPECTED_PERFORMANCE_STATS_VIEW_KEYS)
    assert set(view["queue_counts"].keys()) == {
        "inbox",
        "pending",
        "pending_approvals",
        "done",
        "failed",
        "canceled",
    }
    assert set(view["historical_counters"].keys()) == set(_EXPECTED_HISTORICAL_COUNTER_KEYS)


def test_daemon_health_view_key_set_shape_lock() -> None:
    view = health_view_helpers.daemon_health_view({})
    assert set(view.keys()) == set(_EXPECTED_DAEMON_HEALTH_VIEW_KEYS)


def test_performance_stats_view_history_lines_empty_fallback() -> None:
    view = health_view_helpers.performance_stats_view({}, {})
    recent = view["recent_history"]
    assert recent["last_fallback_line"] == "-"
    assert recent["last_error_line"] == "-"
    assert recent["last_shutdown_line"] == "-"


def test_performance_stats_view_counts_and_historical_counters() -> None:
    view = health_view_helpers.performance_stats_view(
        {
            "counts": {
                "inbox": 2,
                "pending": 3,
                "pending_approvals": 1,
                "done": 10,
                "failed": 4,
                "canceled": 0,
            }
        },
        {
            "counters": {
                "panel_auth_invalid": 3,
                "panel_401_count": 5,
                "brain_fallback_count": 7,
                "brain_fallback_reason_timeout": 2,
            }
        },
    )
    assert view["queue_counts"]["inbox"] == 2
    assert view["queue_counts"]["pending"] == 3
    assert view["queue_counts"]["done"] == 10
    assert view["historical_counters"]["panel_auth_invalid"] == 3
    assert view["historical_counters"]["panel_401_count"] == 5
    assert view["historical_counters"]["brain_fallback_count"] == 7
    assert view["historical_counters"]["brain_fallback_reason_timeout"] == 2
    # Unset counters default to 0.
    assert view["historical_counters"]["panel_429_count"] == 0


def test_panel_app_thin_wrappers_forward_to_helpers() -> None:
    # The thin wrappers must produce byte-for-byte identical results to
    # the extracted helpers when called with the same inputs.
    health_payload = {
        "lock_status": {"status": "held", "pid": 1234, "stale_age_s": 5},
        "last_fallback_to": "primary",
        "last_fallback_reason": "timeout",
        "last_fallback_ts_ms": 1700000000000,
        "daemon_state": "degraded",
    }
    queue_payload = {
        "counts": {
            "inbox": 1,
            "pending": 2,
            "pending_approvals": 0,
            "done": 5,
            "failed": 1,
            "canceled": 0,
        }
    }
    assert panel_module._daemon_health_view(
        health_payload
    ) == health_view_helpers.daemon_health_view(health_payload)
    assert panel_module._performance_stats_view(
        queue_payload, health_payload
    ) == health_view_helpers.performance_stats_view(queue_payload, health_payload)
    assert panel_module._format_ts(1700000000000) == health_view_helpers.format_ts(1700000000000)
    assert panel_module._format_ts(None) == "—"
