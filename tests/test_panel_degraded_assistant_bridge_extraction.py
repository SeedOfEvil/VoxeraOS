"""Extraction-contract tests for the panel degraded-assistant bridge module.

Scope: proves that moving the degraded-assistant bridge / messaging cluster
out of ``voxera.panel.routes_assistant`` into
``voxera.panel.degraded_assistant_bridge`` was done correctly and can't be
silently undone by a later panel-decomposition PR.

These tests are deliberately narrow.  The HTTP-level behavior of
``GET /assistant``, ``POST /assistant/ask``, and the degraded-answer
generation path is already covered end-to-end by ``tests/test_panel.py``;
this file pins the *shape* of the extraction so that the existing
coverage keeps meaning what it used to mean:

1. ``degraded_assistant_bridge.py`` owns the five documented entry points
   ``assistant_stalled_degraded_reason``,
   ``create_panel_assistant_brain``,
   ``generate_degraded_assistant_answer_async``,
   ``generate_degraded_assistant_answer``, and
   ``persist_degraded_assistant_result``.
2. ``panel.app`` still visibly wires those entry points into its
   module-level aliases and thin wrappers (``_assistant_stalled_degraded_reason``,
   ``_create_panel_assistant_brain``, ``_persist_degraded_assistant_result``,
   ``_generate_degraded_assistant_answer``,
   ``_generate_degraded_assistant_answer_async``) and does not
   re-define the bridge logic locally.
3. ``routes_assistant`` imports the bridge entry points from
   ``degraded_assistant_bridge`` and does not locally re-define them.
4. ``degraded_assistant_bridge.py`` does NOT reach back into ``panel.app``
   via any import (AST-level check).
5. Stall-detection semantics are preserved (``assistant_stalled_degraded_reason``).
6. Result persistence semantics are preserved
   (``persist_degraded_assistant_result``).
7. The bridge-patching pattern works: ``panel.app._generate_degraded_assistant_answer``
   pushes monkeypatched ``load_app_config`` and ``_create_panel_assistant_brain``
   into the bridge module before calling.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from voxera.panel import app as panel_module
from voxera.panel import degraded_assistant_bridge, routes_assistant

# ── 1. Bridge module exposes documented entry points ────────────────────


def test_degraded_assistant_bridge_exposes_documented_entry_points() -> None:
    for name in (
        "assistant_stalled_degraded_reason",
        "create_panel_assistant_brain",
        "generate_degraded_assistant_answer_async",
        "generate_degraded_assistant_answer",
        "persist_degraded_assistant_result",
    ):
        assert hasattr(degraded_assistant_bridge, name), (
            f"degraded_assistant_bridge must expose {name!r} as a documented entry point."
        )
        assert callable(getattr(degraded_assistant_bridge, name))


def test_degraded_assistant_bridge_entry_point_signatures() -> None:
    stall_sig = inspect.signature(degraded_assistant_bridge.assistant_stalled_degraded_reason)
    assert list(stall_sig.parameters) == ["context", "request_result", "now_ms"]

    brain_sig = inspect.signature(degraded_assistant_bridge.create_panel_assistant_brain)
    assert list(brain_sig.parameters) == ["provider"]

    persist_sig = inspect.signature(degraded_assistant_bridge.persist_degraded_assistant_result)
    assert "queue_root" in persist_sig.parameters
    assert "request_id" in persist_sig.parameters
    assert "degraded_answer" in persist_sig.parameters

    async_sig = inspect.signature(
        degraded_assistant_bridge.generate_degraded_assistant_answer_async
    )
    assert "question" in async_sig.parameters
    assert "context" in async_sig.parameters
    assert "thread_turns" in async_sig.parameters
    assert "degraded_reason" in async_sig.parameters

    sync_sig = inspect.signature(degraded_assistant_bridge.generate_degraded_assistant_answer)
    assert "question" in sync_sig.parameters
    assert "context" in sync_sig.parameters
    assert "thread_turns" in sync_sig.parameters
    assert "degraded_reason" in sync_sig.parameters


# ── 2. panel.app wires bridge entry points ──────────────────────────────


def test_panel_app_wires_degraded_assistant_bridge_entry_points() -> None:
    # panel.app must expose its module-level aliases and wrappers and each
    # must forward to the extracted bridge function — not re-implement
    # the logic locally.
    for name in (
        "_assistant_stalled_degraded_reason",
        "_create_panel_assistant_brain",
        "_persist_degraded_assistant_result",
        "_generate_degraded_assistant_answer",
        "_generate_degraded_assistant_answer_async",
    ):
        assert callable(getattr(panel_module, name)), f"panel.app must still expose {name}"

    # The aliases must point at the bridge module's functions.
    assert panel_module._assistant_stalled_degraded_reason is (
        degraded_assistant_bridge.assistant_stalled_degraded_reason
    )
    assert panel_module._create_panel_assistant_brain is (
        degraded_assistant_bridge.create_panel_assistant_brain
    )
    assert panel_module._persist_degraded_assistant_result is (
        degraded_assistant_bridge.persist_degraded_assistant_result
    )

    # The wrappers must visibly delegate to the bridge module.
    async_src = inspect.getsource(panel_module._generate_degraded_assistant_answer_async)
    assert "_degraded_assistant_bridge.generate_degraded_assistant_answer_async(" in async_src

    sync_src = inspect.getsource(panel_module._generate_degraded_assistant_answer)
    assert "_generate_degraded_assistant_answer_async(" in sync_src


def test_panel_app_load_app_config_aliases_bridge() -> None:
    assert panel_module.load_app_config is degraded_assistant_bridge.load_app_config


# ── 3. routes_assistant imports from bridge, does not re-define ─────────


def test_routes_assistant_imports_bridge_entry_points() -> None:
    # routes_assistant must import the bridge entry points for its
    # register function defaults, not define them locally.
    for name in (
        "_assistant_stalled_degraded_reason",
        "_generate_degraded_assistant_answer",
        "_generate_degraded_assistant_answer_async",
        "_persist_degraded_assistant_result",
    ):
        attr = getattr(routes_assistant, name, None)
        assert attr is not None, f"routes_assistant must expose {name}"


def test_routes_assistant_does_not_locally_define_extracted_functions() -> None:
    # The implementations that moved to the bridge must NOT be locally
    # defined in routes_assistant anymore.  We check via AST that no
    # top-level function definition in routes_assistant has the extracted
    # names.
    import ast

    src = inspect.getsource(routes_assistant)
    tree = ast.parse(src)

    top_level_defs = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    extracted_names = {
        "_assistant_stalled_degraded_reason",
        "_create_panel_assistant_brain",
        "_degraded_mode_disclosure",
        "_generate_degraded_assistant_answer_async",
        "_generate_degraded_assistant_answer",
        "_persist_degraded_assistant_result",
        "_coerce_int",
        "_assistant_request_ts_ms",
    }

    leaked = top_level_defs & extracted_names
    assert not leaked, (
        f"routes_assistant should no longer define {leaked!r} locally; "
        "they were extracted to voxera.panel.degraded_assistant_bridge."
    )


# ── 4. panel.app does not re-define extracted private helpers ───────────


def test_panel_app_does_not_redefine_extracted_bridge_helpers() -> None:
    for name in (
        "_degraded_mode_disclosure",
        "_coerce_int",
        "_assistant_request_ts_ms",
        "_ASSISTANT_STALL_TIMEOUT_MS",
        "_ASSISTANT_FALLBACK_REASONS",
        "_ASSISTANT_UNAVAILABLE_STATES",
    ):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer own {name!r}; it was extracted to "
            "voxera.panel.degraded_assistant_bridge."
        )


# ── 5. Bridge module does not reach back into panel.app ─────────────────


def test_degraded_assistant_bridge_does_not_reach_back_into_panel_app() -> None:
    import ast

    tree = ast.parse(inspect.getsource(degraded_assistant_bridge))
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


# ── 6. Stall-detection semantics preserved ──────────────────────────────


def test_assistant_stalled_degraded_reason_returns_none_for_empty_result() -> None:
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason({}, {}, now_ms=1_000_000)
        is None
    )


def test_assistant_stalled_degraded_reason_returns_none_for_answered() -> None:
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {},
            {"status": "queued", "answer": "some answer"},
            now_ms=1_000_000,
        )
        is None
    )


def test_assistant_stalled_degraded_reason_returns_none_for_degraded_mode() -> None:
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {},
            {"status": "queued", "advisory_mode": "degraded_brain_only"},
            now_ms=1_000_000,
        )
        is None
    )


def test_assistant_stalled_degraded_reason_daemon_paused() -> None:
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {"queue_paused": True},
            {"status": "queued"},
            now_ms=1_000_000,
        )
        == "daemon_paused"
    )


def test_assistant_stalled_degraded_reason_daemon_unavailable() -> None:
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {"health_current_state": {"daemon_state": "stopped"}},
            {"status": "queued"},
            now_ms=1_000_000,
        )
        == "daemon_unavailable"
    )


def test_assistant_stalled_degraded_reason_queue_processing_timeout() -> None:
    now_ms = 1_000_000
    stale_ms = now_ms - 200_000
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {"health_current_state": {"daemon_state": "healthy"}},
            {"status": "thinking through Voxera", "updated_at_ms": stale_ms},
            now_ms=now_ms,
        )
        == "queue_processing_timeout"
    )


def test_assistant_stalled_degraded_reason_advisory_transport_stalled() -> None:
    now_ms = 1_000_000
    old_ts = now_ms - 200_000
    assert (
        degraded_assistant_bridge.assistant_stalled_degraded_reason(
            {"health_current_state": {"daemon_state": "healthy"}},
            {"status": "queued", "request_id": f"job-assistant-{old_ts}.json"},
            now_ms=now_ms,
        )
        == "advisory_transport_stalled"
    )


# ── 7. Persistence semantics preserved ──────────────────────────────────


def test_persist_degraded_assistant_result_writes_artifact(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    result = degraded_assistant_bridge.persist_degraded_assistant_result(
        queue_root,
        request_id="job-assistant-12345.json",
        thread_id="thread-abc",
        question="What is happening?",
        degraded_answer={
            "answer": "Disclosure.\n\nDeterministic answer.",
            "provider": "deterministic_fallback",
            "model": "fallback_operator_answer",
            "fallback_used": False,
            "fallback_reason": "UNKNOWN",
            "error_class": "RuntimeError",
            "deterministic_used": True,
        },
        degraded_reason="daemon_paused",
        context={"queue_paused": True},
        ts_ms=12345,
    )
    artifact_file = queue_root / "artifacts" / "job-assistant-12345" / "assistant_response.json"
    assert artifact_file.exists()
    on_disk = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert on_disk["advisory_mode"] == "degraded_brain_only"
    assert on_disk["degraded_reason"] == "daemon_paused"
    assert on_disk["thread_id"] == "thread-abc"
    assert on_disk["schema_version"] == 2
    assert on_disk["kind"] == "assistant_question"
    assert on_disk["deterministic_fallback_used"] is True
    assert result == on_disk


# ── 8. Bridge-patching pattern works ────────────────────────────────────


def test_panel_app_async_wrapper_patches_bridge_module(monkeypatch) -> None:
    # Verify that the async wrapper in panel.app pushes monkeypatched
    # values into the bridge module before calling.
    async_src = inspect.getsource(panel_module._generate_degraded_assistant_answer_async)
    assert "_degraded_assistant_bridge.load_app_config = load_app_config" in async_src
    assert (
        "_degraded_assistant_bridge.create_panel_assistant_brain = _create_panel_assistant_brain"
        in async_src
    )


def test_bridge_patching_pattern_preserves_monkeypatch_flow(monkeypatch) -> None:
    # End-to-end: monkeypatch panel_module.load_app_config and
    # panel_module._create_panel_assistant_brain, then call
    # panel_module._generate_degraded_assistant_answer.
    # The patched values must flow through to the bridge module.
    from types import SimpleNamespace

    monkeypatch.setattr(
        panel_module,
        "load_app_config",
        lambda: SimpleNamespace(
            brain={
                "primary": SimpleNamespace(
                    type="openai_compat",
                    model="test-model",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
            }
        ),
    )

    class _TestBrain:
        async def generate(self, messages, tools=None):
            return SimpleNamespace(text="bridge test answer")

    monkeypatch.setattr(
        panel_module, "_create_panel_assistant_brain", lambda provider: _TestBrain()
    )

    result = panel_module._generate_degraded_assistant_answer(
        "Test question",
        {"health_current_state": {"daemon_state": "healthy"}},
        thread_turns=[],
        degraded_reason="daemon_paused",
    )
    assert result["provider"] == "primary"
    assert result["model"] == "test-model"
    assert "bridge test answer" in result["answer"]
    assert "model-only recovery mode" in result["answer"]
