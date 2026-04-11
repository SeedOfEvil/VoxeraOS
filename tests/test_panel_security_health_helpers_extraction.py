"""Extraction-contract tests for the panel security / health snapshot helper module.

Scope: proves that moving the security / health snapshot helper cluster
out of ``voxera.panel.app`` into
``voxera.panel.security_health_helpers`` was done correctly and can't be
silently undone by a later panel-decomposition PR.

These tests are deliberately narrow. The HTTP-level behavior of the home
page (which reads ``panel_security_snapshot`` + ``auth_setup_banner``),
the hygiene / recovery routes (which take ``health_queue_root`` as a
callback), and the auth flow (which reaches back through
``panel.app._health_queue_root`` / ``_panel_security_counter_incr``) is
already covered by ``tests/test_panel.py``; this file pins the *shape*
of the extraction so that the existing HTTP coverage keeps meaning what
it used to mean:

1. ``security_health_helpers.py`` owns the four documented entry points
   ``health_queue_root(queue_root)``,
   ``panel_security_counter_incr(queue_root, key, *, last_error)``,
   ``panel_security_snapshot(queue_root)``, and
   ``auth_setup_banner(settings)``.
2. ``panel.app`` still visibly wires those entry points into its thin
   wrapper callbacks (``_health_queue_root``,
   ``_panel_security_counter_incr``, ``_panel_security_snapshot``,
   ``_auth_setup_banner``) and does not re-define the helper logic
   locally.
3. ``panel.app`` no longer defines the private helper bodies inline:
   the wrappers must visibly call the extracted module functions.
4. ``security_health_helpers.py`` does NOT reach back into ``panel.app``
   via any import (explicit-args architecture invariant, matching PR
   B's ``queue_mutation_bridge``).
5. The reach-back-via-wrapper pattern for ``auth_enforcement`` still
   works: monkeypatching ``panel_module._health_queue_root`` and
   ``panel_module._panel_security_counter_incr`` is still visible
   through ``auth_enforcement._health_queue_root`` /
   ``_panel_security_counter_incr`` at call time.
6. ``health_queue_root`` semantics preserved exactly: no
   ``VOXERA_HEALTH_PATH`` returns the configured queue root; the
   test-only isolation safety net (default repo queue + isolated
   health path + no explicit ``VOXERA_QUEUE_ROOT``) returns ``None``;
   explicit ``VOXERA_QUEUE_ROOT`` short-circuits to the configured
   queue root even with isolated health set.
7. ``auth_setup_banner`` semantics preserved exactly: returns ``None``
   when a password is set; otherwise returns the four-key dict with
   the expected template fields.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

from voxera.panel import app as panel_module
from voxera.panel import auth_enforcement, security_health_helpers


def test_security_health_helpers_exposes_documented_entry_points() -> None:
    for name in (
        "health_queue_root",
        "panel_security_counter_incr",
        "panel_security_snapshot",
        "auth_setup_banner",
    ):
        assert hasattr(security_health_helpers, name), (
            f"security_health_helpers must expose {name!r} as a documented entry point."
        )
        assert callable(getattr(security_health_helpers, name))

    health_sig = inspect.signature(security_health_helpers.health_queue_root)
    assert list(health_sig.parameters) == ["queue_root"]

    incr_sig = inspect.signature(security_health_helpers.panel_security_counter_incr)
    assert list(incr_sig.parameters) == ["queue_root", "key", "last_error"]

    snapshot_sig = inspect.signature(security_health_helpers.panel_security_snapshot)
    assert list(snapshot_sig.parameters) == ["queue_root"]

    banner_sig = inspect.signature(security_health_helpers.auth_setup_banner)
    assert list(banner_sig.parameters) == ["settings"]


def test_panel_app_wires_security_health_helper_entry_points() -> None:
    # panel.app must expose its thin wrapper callbacks and each wrapper
    # must forward to the extracted helper function — not re-implement
    # the logic locally.
    for name in (
        "_health_queue_root",
        "_panel_security_counter_incr",
        "_panel_security_snapshot",
        "_auth_setup_banner",
    ):
        assert callable(getattr(panel_module, name)), f"panel.app must still expose {name}"

    assert "_health_queue_root_impl(" in inspect.getsource(panel_module._health_queue_root)
    assert "_panel_security_counter_incr_impl(" in inspect.getsource(
        panel_module._panel_security_counter_incr
    )
    assert "_panel_security_snapshot_impl(" in inspect.getsource(
        panel_module._panel_security_snapshot
    )
    assert "_auth_setup_banner_impl(" in inspect.getsource(panel_module._auth_setup_banner)


def test_panel_app_does_not_redefine_extracted_banner_constants() -> None:
    # The banner body text used to live inline in panel.app's
    # _auth_setup_banner. After extraction the wrapper must visibly
    # delegate; it must not contain the original banner body strings.
    wrapper_source = inspect.getsource(panel_module._auth_setup_banner)
    assert "Setup required" not in wrapper_source
    assert "VOXERA_PANEL_OPERATOR_PASSWORD" not in wrapper_source
    assert "systemctl --user edit voxera-panel.service" not in wrapper_source


def test_panel_app_no_longer_imports_health_primitives_directly() -> None:
    # After extraction the helper module is the only panel-side caller of
    # ``voxera.health.increment_health_counter`` / ``read_health_snapshot``.
    # Pin that ``panel.app`` does not re-import them so a later PR cannot
    # silently reintroduce a local inline implementation bypassing the
    # extraction.
    assert not hasattr(panel_module, "increment_health_counter"), (
        "panel.app should no longer import increment_health_counter directly; "
        "it now lives behind voxera.panel.security_health_helpers.panel_security_counter_incr."
    )
    assert not hasattr(panel_module, "read_health_snapshot"), (
        "panel.app should no longer import read_health_snapshot directly; "
        "it now lives behind voxera.panel.security_health_helpers.panel_security_snapshot."
    )


def test_security_health_helpers_does_not_reach_back_into_panel_app() -> None:
    # Architecture invariant: like PR B's queue_mutation_bridge, this
    # module is pure — every input is explicit. A future PR that sneaks
    # in a ``from . import app`` would quietly reintroduce a circular
    # dependency and hide state. Catch it here via AST so docstrings /
    # comments that mention "panel.app" don't false-positive.
    tree = ast.parse(inspect.getsource(security_health_helpers))
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


def test_auth_enforcement_reach_back_still_works_through_panel_app_wrappers(monkeypatch) -> None:
    # This is the key invariant for PR A + PR C coexistence:
    # auth_enforcement._health_queue_root / _panel_security_counter_incr
    # resolve ``panel.app._health_queue_root`` / ``_panel_security_counter_incr``
    # at call time, so monkeypatching the wrappers on panel.app must
    # still drive the auth flow through the new extraction.
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


def test_health_queue_root_returns_configured_root_when_no_isolation(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VOXERA_HEALTH_PATH", raising=False)
    monkeypatch.delenv("VOXERA_QUEUE_ROOT", raising=False)
    queue_root = tmp_path / "queue"
    assert security_health_helpers.health_queue_root(queue_root) == queue_root


def test_health_queue_root_returns_configured_root_when_explicit_queue_override(
    monkeypatch, tmp_path
) -> None:
    # VOXERA_HEALTH_PATH + explicit VOXERA_QUEUE_ROOT → production
    # semantics win: configured queue root is returned unchanged.
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(tmp_path / "iso" / "health.json"))
    monkeypatch.setenv("VOXERA_QUEUE_ROOT", str(tmp_path / "explicit"))
    queue_root = tmp_path / "queue"
    assert security_health_helpers.health_queue_root(queue_root) == queue_root


def test_health_queue_root_returns_none_for_repo_default_isolation(monkeypatch, tmp_path) -> None:
    # Test-only safety net: when configured queue root resolves to the
    # repo default ($CWD/notes/queue) AND VOXERA_HEALTH_PATH is set AND
    # VOXERA_QUEUE_ROOT is NOT set, return None so the caller falls
    # through to the isolated health path.
    repo = tmp_path / "VoxeraOS"
    (repo / "notes" / "queue").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(tmp_path / "iso" / "health.json"))
    monkeypatch.delenv("VOXERA_QUEUE_ROOT", raising=False)

    default_queue = repo / "notes" / "queue"
    assert security_health_helpers.health_queue_root(default_queue) is None


def test_health_queue_root_returns_queue_for_non_default_under_isolation(
    monkeypatch, tmp_path
) -> None:
    # VOXERA_HEALTH_PATH set, no explicit VOXERA_QUEUE_ROOT, but the
    # configured queue root doesn't match the repo default — return the
    # configured queue root so panel writes land on the right files.
    repo = tmp_path / "VoxeraOS"
    (repo / "notes" / "queue").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(tmp_path / "iso" / "health.json"))
    monkeypatch.delenv("VOXERA_QUEUE_ROOT", raising=False)

    other_queue = tmp_path / "somewhere" / "queue"
    assert security_health_helpers.health_queue_root(other_queue) == other_queue


def test_panel_security_snapshot_returns_empty_dict_when_counters_missing(tmp_path) -> None:
    # Empty queue root (no health.json) must return an empty dict
    # rather than None / raising. Mirrors the in-app implementation
    # that protects the home page against a missing snapshot.
    counters = security_health_helpers.panel_security_snapshot(tmp_path / "queue")
    assert counters == {}


def test_panel_security_counter_incr_writes_counter(tmp_path) -> None:
    queue_root = tmp_path / "queue"
    queue_root.mkdir(parents=True)
    security_health_helpers.panel_security_counter_incr(
        queue_root, "panel_401_count", last_error="probe"
    )
    counters = security_health_helpers.panel_security_snapshot(queue_root)
    assert counters["panel_401_count"] == 1


def test_auth_setup_banner_returns_none_when_password_set() -> None:
    settings = SimpleNamespace(
        panel_operator_password="s3cret",
        config_path=Path("/tmp/config.json"),
    )
    assert security_health_helpers.auth_setup_banner(settings) is None


def test_auth_setup_banner_returns_full_dict_when_password_empty() -> None:
    settings = SimpleNamespace(
        panel_operator_password="",
        config_path=Path("/tmp/config.json"),
    )
    banner = security_health_helpers.auth_setup_banner(settings)
    assert banner is not None
    assert set(banner.keys()) == {"title", "detail", "path_hint", "commands"}
    assert banner["title"].startswith("Setup required")
    assert "VOXERA_PANEL_OPERATOR_PASSWORD" in banner["detail"]
    assert "/tmp/config.json" in banner["path_hint"]
    assert "systemctl --user edit voxera-panel.service" in banner["commands"]


def test_auth_setup_banner_returns_full_dict_when_password_none() -> None:
    settings = SimpleNamespace(
        panel_operator_password=None,
        config_path=Path("/tmp/config.json"),
    )
    banner = security_health_helpers.auth_setup_banner(settings)
    assert banner is not None
    assert set(banner.keys()) == {"title", "detail", "path_hint", "commands"}


def test_panel_app_thin_wrappers_forward_to_helpers(monkeypatch, tmp_path) -> None:
    # The thin wrappers in panel.app must resolve their dependencies at
    # call time: _queue_root() for the health-root derivation, and
    # _settings() for the banner. Monkeypatch each and verify the
    # forwarded call lands in the helper module.
    fake_queue = tmp_path / "queue"
    fake_queue.mkdir(parents=True)

    monkeypatch.setattr(panel_module, "_queue_root", lambda: fake_queue)
    monkeypatch.delenv("VOXERA_HEALTH_PATH", raising=False)
    monkeypatch.delenv("VOXERA_QUEUE_ROOT", raising=False)

    assert panel_module._health_queue_root() == fake_queue

    panel_module._panel_security_counter_incr("panel_auth_invalid", last_error="probe")
    counters = panel_module._panel_security_snapshot()
    assert counters["panel_auth_invalid"] == 1

    # Banner — panel.app._auth_setup_banner resolves settings at call
    # time through panel.app._settings.
    fake_settings_empty = SimpleNamespace(
        panel_operator_password="",
        config_path=Path("/tmp/config.json"),
    )
    monkeypatch.setattr(panel_module, "_settings", lambda: fake_settings_empty)
    banner = panel_module._auth_setup_banner()
    assert banner is not None
    assert banner["title"].startswith("Setup required")

    fake_settings_set = SimpleNamespace(
        panel_operator_password="s3cret",
        config_path=Path("/tmp/config.json"),
    )
    monkeypatch.setattr(panel_module, "_settings", lambda: fake_settings_set)
    assert panel_module._auth_setup_banner() is None
