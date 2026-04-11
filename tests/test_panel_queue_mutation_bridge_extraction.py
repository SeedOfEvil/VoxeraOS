"""Extraction-contract tests for the panel queue mutation bridge module.

Scope: proves that moving the hygiene / queue mutation bridge cluster out
of ``voxera.panel.app`` into ``voxera.panel.queue_mutation_bridge`` was
done correctly and can't be silently undone by a later panel-decomposition
PR.

These tests are deliberately narrow. The HTTP-level behavior of
``/hygiene/prune-dry-run``, ``/hygiene/reconcile``, ``/queue/create`` and
``/missions/create`` is already covered end-to-end by ``tests/test_panel.py``;
this file pins the *shape* of the extraction so that the existing
coverage keeps meaning what it used to mean:

1. ``queue_mutation_bridge.py`` owns the two documented entry points
   ``run_queue_hygiene_command(...)`` and ``write_panel_mission_job(...)``
   plus the ``write_queue_job`` / ``write_hygiene_result`` bridge helpers
   used by the hygiene and home route registrations.
2. ``panel.app`` still visibly wires those entry points into its route
   registration callbacks (``_write_queue_job``, ``_write_panel_mission_job``,
   ``_run_queue_hygiene_command``, ``_write_hygiene_result``) and does
   not re-define the bridge logic locally.
3. ``panel.app`` does NOT locally re-define the extracted private helpers
   (``_trim_tail``, ``_repo_root_for_panel_subprocess``, the ``enrich``
   / ``update_health_snapshot`` / ``uuid`` / ``hashlib`` imports).
4. Queue-truth semantics: ``write_queue_job`` and
   ``write_panel_mission_job`` write atomically through
   ``enrich_queue_job_payload`` with the preserved ``source_lane``
   envelopes (``panel_queue_create`` and ``panel_mission_prompt``), and
   the resulting payload lands on disk under ``inbox/``.
5. Fail-closed ``run_queue_hygiene_command``: CLI subprocess failures and
   malformed JSON outputs surface as ``ok=False`` result dicts with a
   non-empty ``error`` field — never as exceptions.
6. ``write_hygiene_result`` honours the caller-supplied ``now_ms``
   callable (the seam that keeps ``panel.app._now_ms`` monkeypatching
   valid through the thin wrapper).
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

from voxera.panel import app as panel_module
from voxera.panel import queue_mutation_bridge


def test_queue_mutation_bridge_exposes_documented_entry_points() -> None:
    # Two narrow entry points plus the two bridge helpers used by the
    # hygiene / home route registrations.
    for name in (
        "run_queue_hygiene_command",
        "write_panel_mission_job",
        "write_queue_job",
        "write_hygiene_result",
    ):
        assert hasattr(queue_mutation_bridge, name), (
            f"queue_mutation_bridge must expose {name!r} as a documented entry point."
        )
        assert callable(getattr(queue_mutation_bridge, name))

    run_sig = inspect.signature(queue_mutation_bridge.run_queue_hygiene_command)
    assert list(run_sig.parameters) == ["queue_root", "args"]

    mission_sig = inspect.signature(queue_mutation_bridge.write_panel_mission_job)
    assert list(mission_sig.parameters) == ["queue_root", "prompt", "approval_required"]

    write_job_sig = inspect.signature(queue_mutation_bridge.write_queue_job)
    assert list(write_job_sig.parameters) == ["queue_root", "payload"]

    # write_hygiene_result takes the ``now_ms`` callable as an explicit
    # keyword-only arg — that's the seam that keeps ``panel.app._now_ms``
    # monkeypatching valid through the thin wrapper in panel.app.
    write_hygiene_sig = inspect.signature(queue_mutation_bridge.write_hygiene_result)
    assert list(write_hygiene_sig.parameters) == ["queue_root", "key", "result", "now_ms"]


def test_panel_app_wires_queue_mutation_bridge_entry_points() -> None:
    # panel.app must expose its route-registration callback wrappers and
    # each wrapper must forward to the extracted bridge function — not
    # re-implement the logic locally.
    for name in (
        "_write_queue_job",
        "_write_panel_mission_job",
        "_run_queue_hygiene_command",
        "_write_hygiene_result",
    ):
        assert callable(getattr(panel_module, name)), f"panel.app must still expose {name}"

    assert "write_queue_job(" in inspect.getsource(panel_module._write_queue_job)
    assert "write_panel_mission_job(" in inspect.getsource(panel_module._write_panel_mission_job)
    assert "run_queue_hygiene_command(" in inspect.getsource(
        panel_module._run_queue_hygiene_command
    )
    assert "write_hygiene_result(" in inspect.getsource(panel_module._write_hygiene_result)


def test_panel_app_does_not_redefine_extracted_bridge_helpers() -> None:
    # The private helpers that moved out must not be re-introduced as
    # locally defined functions on panel.app. (Re-exports via
    # ``from ... import`` are fine and are checked by the positive
    # assertions above.)
    for name in ("_trim_tail", "_repo_root_for_panel_subprocess"):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer own {name!r}; it was extracted to "
            "voxera.panel.queue_mutation_bridge as part of PR B."
        )


def test_panel_app_still_exposes_subprocess_and_sys_for_hygiene_monkeypatch() -> None:
    # tests/test_panel.py::test_hygiene_* monkeypatches panel.app via
    # ``monkeypatch.setattr(panel_module.subprocess, "run", _fake_run)`` and
    # asserts ``panel_module.sys.executable``. Both are module singletons so
    # mutating ``panel_module.subprocess.run`` affects the global
    # ``subprocess.run`` that queue_mutation_bridge actually calls. Pin the
    # re-export surface so a later PR cannot silently drop the kept-for-
    # test-compat imports and break every hygiene test at once.
    assert panel_module.subprocess is subprocess
    assert panel_module.sys is sys


def test_queue_mutation_bridge_does_not_reach_back_into_panel_app() -> None:
    # Architecture invariant: unlike PR A's auth_enforcement (which
    # deliberately reaches back through panel.app for shared wrappers),
    # the mutation bridge is pure — every input is explicit. A future PR
    # that sneaks in a ``from . import app`` would quietly reintroduce a
    # circular dependency and hide state. Catch it here via AST so
    # docstrings / comments that mention "panel.app" don't false-positive.
    import ast

    tree = ast.parse(inspect.getsource(queue_mutation_bridge))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # ``from .app import ...`` would show up as module="app" with
            # level=1; ``from voxera.panel.app import ...`` as module=
            # "voxera.panel.app" with level=0. Guard both spellings.
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


def test_write_queue_job_preserves_source_lane_and_atomic_write(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    created = queue_mutation_bridge.write_queue_job(
        queue_root,
        {"id": "probe-job", "goal": "demo"},
    )
    final = queue_root / "inbox" / created
    assert final.exists()
    payload = json.loads(final.read_text(encoding="utf-8"))
    assert payload["goal"] == "demo"
    # The job_intent envelope must be preserved exactly as the previous
    # in-app._write_queue_job produced it.
    assert payload["job_intent"]["source_lane"] == "panel_queue_create"
    # No stray tmp file left behind.
    tmp_siblings = [p for p in (queue_root / "inbox").iterdir() if p.name.startswith(".")]
    assert tmp_siblings == []


def test_write_panel_mission_job_preserves_queue_truth_envelope(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    filename, mission_id = queue_mutation_bridge.write_panel_mission_job(
        queue_root,
        prompt="run system check",
        approval_required=True,
    )
    final = queue_root / "inbox" / filename
    assert final.exists()
    payload = json.loads(final.read_text(encoding="utf-8"))
    assert payload["id"] == mission_id
    assert payload["goal"] == "run system check"
    assert payload["approval_required"] is True
    assert payload["approval_hints"] == ["manual"]
    assert payload["expected_artifacts"] == [
        "plan.json",
        "execution_envelope.json",
        "execution_result.json",
        "step_results.json",
    ]
    # source_lane envelope MUST remain ``panel_mission_prompt`` — this is
    # the contract that lets downstream lanes recognize panel-submitted
    # mission prompts vs other queue-create intents.
    assert payload["job_intent"]["source_lane"] == "panel_mission_prompt"


def test_run_queue_hygiene_command_fail_closed_on_non_zero_exit(monkeypatch, tmp_path) -> None:
    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "boom: missing config"

    monkeypatch.setattr(
        queue_mutation_bridge.subprocess,
        "run",
        lambda *args, **kwargs: _Proc(),
    )
    result = queue_mutation_bridge.run_queue_hygiene_command(
        tmp_path / "queue",
        ["queue", "prune", "--json"],
    )
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "boom" in result["stderr_tail"]
    assert result["error"]


def test_run_queue_hygiene_command_fail_closed_on_invalid_json(monkeypatch, tmp_path) -> None:
    class _Proc:
        returncode = 0
        stdout = "{not-json}"
        stderr = ""

    monkeypatch.setattr(
        queue_mutation_bridge.subprocess,
        "run",
        lambda *args, **kwargs: _Proc(),
    )
    result = queue_mutation_bridge.run_queue_hygiene_command(
        tmp_path / "queue",
        ["queue", "reconcile", "--json"],
    )
    assert result["ok"] is False
    assert result["error"] == "json parse failed"
    assert result["stdout_tail"] == "{not-json}"


def test_write_hygiene_result_uses_injected_now_ms(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    queue_root.mkdir(parents=True)
    (queue_root / "health.json").write_text("{}", encoding="utf-8")

    queue_mutation_bridge.write_hygiene_result(
        queue_root,
        "last_prune_result",
        {"ok": True, "would_remove_jobs": 0},
        now_ms=lambda: 4_242_424_242,
    )
    health = json.loads((queue_root / "health.json").read_text(encoding="utf-8"))
    assert health["last_prune_result"]["would_remove_jobs"] == 0
    assert health["updated_at_ms"] == 4_242_424_242


def test_panel_app_thin_wrapper_still_honours_now_ms_monkeypatch(tmp_path, monkeypatch) -> None:
    # This is the key reach-back invariant: panel.app._write_hygiene_result
    # resolves ``_now_ms`` at call time through its module globals, so
    # ``monkeypatch.setattr(panel_module, "_now_ms", ...)`` still drives
    # the ``updated_at_ms`` stamp exactly as before the extraction.
    queue_root = tmp_path / "queue"
    queue_root.mkdir(parents=True)
    (queue_root / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module, "_now_ms", lambda: 111_222_333)

    panel_module._write_hygiene_result(
        queue_root,
        "last_reconcile_result",
        {"ok": True, "issue_counts": {}},
    )
    health = json.loads((queue_root / "health.json").read_text(encoding="utf-8"))
    assert health["last_reconcile_result"]["ok"] is True
    assert health["updated_at_ms"] == 111_222_333
