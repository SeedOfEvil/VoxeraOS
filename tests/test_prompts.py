from pathlib import Path

import pytest

from voxera import prompts
from voxera.core.planner_context import get_planner_preamble
from voxera.operator_assistant import build_assistant_messages
from voxera.vera import prompt as vera_prompt
from voxera.vera.service import _CODE_DRAFT_HINT, _WRITING_DRAFT_HINT


def test_load_prompt_doc_reads_expected_markdown() -> None:
    text = prompts.load_prompt_doc("00-system-overview.md")
    assert "# System Overview" in text


def test_compose_prompt_docs_uses_deterministic_order() -> None:
    composed = prompts.compose_prompt_docs("02-role-map.md", "01-platform-boundaries.md")
    assert composed.index("# Role Map") < composed.index("# Platform Boundaries")


def test_load_prompt_doc_missing_path_fails_clearly() -> None:
    with pytest.raises(FileNotFoundError, match="Prompt doc not found"):
        prompts.load_prompt_doc("capabilities/does-not-exist.md")


def test_load_prompt_doc_path_escape_rejected() -> None:
    with pytest.raises(ValueError, match="escapes docs/prompts"):
        prompts.load_prompt_doc("../README.md")


def test_compose_hidden_compiler_prompt_includes_rich_capabilities() -> None:
    prompt_text = prompts.compose_hidden_compiler_prompt()
    assert "# Hidden Compiler Role" in prompt_text
    assert "# Capability: Preview Payload Schema" in prompt_text
    assert "# Capability: Queue Lifecycle" in prompt_text
    assert "# Capability: Artifacts and Evidence" in prompt_text
    assert "# Capability: Execution Security Model" in prompt_text
    assert "# Capability: Hidden Compiler Payload Guidance" in prompt_text


def test_compose_hidden_compiler_prompt_order_is_deterministic() -> None:
    prompt_text = prompts.compose_hidden_compiler_prompt()
    assert prompt_text.index("# System Overview") < prompt_text.index("# Hidden Compiler Role")
    assert prompt_text.index("# Hidden Compiler Role") < prompt_text.index(
        "# Capability: Preview Payload Schema"
    )
    assert prompt_text.index("# Capability: Preview Payload Schema") < prompt_text.index(
        "# Capability: Hidden Compiler Payload Guidance"
    )


def test_vera_prompts_are_doc_composed() -> None:
    assert "# Vera Role" in vera_prompt.VERA_SYSTEM_PROMPT
    assert "# Capability: Handoff and Submit Rules" in vera_prompt.VERA_SYSTEM_PROMPT
    assert "# Capability: Web Investigation Rules" in vera_prompt.VERA_SYSTEM_PROMPT

    assert "# Hidden Compiler Role" in vera_prompt.VERA_PREVIEW_BUILDER_PROMPT
    assert (
        "# Capability: Hidden Compiler Payload Guidance" in vera_prompt.VERA_PREVIEW_BUILDER_PROMPT
    )


def test_planner_preamble_includes_prompt_docs() -> None:
    preamble = get_planner_preamble(env={})
    assert "# Planner Role" in preamble
    assert "# Capability: Queue Lifecycle" in preamble
    assert "# Capability: Artifacts and Evidence" in preamble
    assert "# Capability: Execution Security Model" in preamble


def test_all_prompt_docs_exist_for_declared_roles() -> None:
    root = Path("docs/prompts")
    for role, role_doc in prompts._ROLE_DOCS.items():  # noqa: SLF001
        assert (root / role_doc).exists(), role
        for capability_doc in prompts._ROLE_CAPABILITY_DOCS[role]:  # noqa: SLF001
            assert (root / capability_doc).exists(), f"{role}: {capability_doc}"


# ── Output-quality defaults wiring ──────────────────────────────────────


def test_output_quality_defaults_doc_exists() -> None:
    root = Path("docs/prompts")
    assert (root / "capabilities" / "output-quality-defaults.md").exists()


def test_output_quality_defaults_wired_to_all_roles() -> None:
    for role in prompts._ROLE_DOCS:  # noqa: SLF001
        caps = prompts._ROLE_CAPABILITY_DOCS[role]  # noqa: SLF001
        assert "capabilities/output-quality-defaults.md" in caps, (
            f"output-quality-defaults.md not wired to role: {role}"
        )


def test_all_composed_prompts_include_output_quality_section() -> None:
    for role, compose_fn in [
        ("vera", prompts.compose_vera_prompt),
        ("hidden_compiler", prompts.compose_hidden_compiler_prompt),
        ("planner", prompts.compose_planner_prompt),
        ("verifier", prompts.compose_verifier_prompt),
        ("web_investigator", prompts.compose_web_investigator_prompt),
    ]:
        text = compose_fn()
        assert "# Capability: Output Quality Defaults" in text, (
            f"composed {role} prompt missing output quality section"
        )


# ── Automation awareness in shared prompts ──────────────────────────────


def test_system_overview_mentions_automation_definitions() -> None:
    text = prompts.load_prompt_doc("00-system-overview.md")
    assert "automation" in text.lower()


def test_platform_boundaries_mention_automation_save_execute() -> None:
    text = prompts.load_prompt_doc("01-platform-boundaries.md")
    assert "saving an automation definition is not executing it" in text.lower()


def test_runtime_overview_covers_automation_subsystem() -> None:
    text = prompts.load_prompt_doc("03-runtime-technical-overview.md")
    assert "Automation Subsystem" in text
    assert "automation runner" in text.lower()
    assert "recurring_cron" in text
    assert "watch_path" in text


def test_vera_role_includes_automation_lifecycle() -> None:
    text = prompts.load_prompt_doc("roles/vera.md")
    assert "automation lifecycle" in text.lower()
    assert "enable" in text.lower()
    assert "disable" in text.lower()
    assert "run-now" in text.lower() or "force-run" in text.lower()


def test_role_map_mentions_automation_for_vera() -> None:
    text = prompts.load_prompt_doc("02-role-map.md")
    assert "automation" in text.lower()


# ── Unsupported features are not marked as active ───────────────────────


def test_runtime_overview_marks_cron_and_watch_path_as_not_active() -> None:
    text = prompts.load_prompt_doc("03-runtime-technical-overview.md")
    # Both should be marked as "not yet active" or similar
    cron_idx = text.index("recurring_cron")
    watch_idx = text.index("watch_path")
    # Check within 200 chars after the keyword
    cron_context = text[cron_idx : cron_idx + 200].lower()
    watch_context = text[watch_idx : watch_idx + 200].lower()
    assert "not yet active" in cron_context or "skipped" in cron_context
    assert "not yet active" in watch_context or "skipped" in watch_context


# ── Save vs execute wording is explicit ─────────────────────────────────


def test_vera_role_distinguishes_save_from_execute() -> None:
    text = prompts.load_prompt_doc("roles/vera.md")
    assert "not emit a queue job" in text.lower() or "does not execute" in text.lower()


def test_planner_role_includes_quality_guidance() -> None:
    text = prompts.load_prompt_doc("roles/planner.md")
    assert "plan quality" in text.lower() or "actionable" in text.lower()


# ── Composed prompts are non-empty and structured ───────────────────────


def test_all_composed_prompts_produce_nonempty_output() -> None:
    for name, fn in [
        ("vera", prompts.compose_vera_prompt),
        ("hidden_compiler", prompts.compose_hidden_compiler_prompt),
        ("planner", prompts.compose_planner_prompt),
        ("verifier", prompts.compose_verifier_prompt),
        ("web_investigator", prompts.compose_web_investigator_prompt),
    ]:
        text = fn()
        assert len(text) > 500, f"{name} composed prompt is unexpectedly short ({len(text)} chars)"
        # Every composed prompt should start with the system overview
        assert text.startswith("# System Overview"), (
            f"{name} prompt does not start with System Overview"
        )


# ── Code-level inline instruction surfaces ──────────────────────────────


def test_code_draft_hint_contains_quality_guidance() -> None:
    hint = _CODE_DRAFT_HINT.lower()
    assert "complete" in hint, "code draft hint should guide toward complete code"
    assert "import" in hint, "code draft hint should mention imports"
    assert "error handling" in hint, "code draft hint should mention error handling"


def test_writing_draft_hint_contains_depth_guidance() -> None:
    hint = _WRITING_DRAFT_HINT.lower()
    assert "length" in hint or "depth" in hint, (
        "writing draft hint should guide toward honoring depth"
    )
    assert "tone" in hint, "writing draft hint should mention tone"
    assert "section" in hint or "structure" in hint, (
        "writing draft hint should mention structure for longer pieces"
    )


def test_operator_assistant_system_prompt_contains_expected_guidance() -> None:
    messages = build_assistant_messages("test question", {"queue_counts": {}})
    system_msg = messages[0]["content"]
    lowered = system_msg.lower()
    assert "automation" in lowered, "operator assistant should mention automations"
    assert "lifecycle" in lowered or (
        "queued" in lowered and "planning" in lowered and "running" in lowered
    ), "operator assistant should reference lifecycle terms"
    assert "advisory" in lowered, "operator assistant should state advisory-only lane"
    assert "saving" in lowered or "not executing" in lowered, (
        "operator assistant should distinguish saving from executing"
    )


def test_planner_preamble_includes_output_quality_section() -> None:
    preamble = get_planner_preamble(env={})
    assert "# Capability: Output Quality Defaults" in preamble


# ── Vera decomposition coverage in runtime overview ─────────────────────


def test_runtime_overview_vera_decomposition_includes_automation_modules() -> None:
    text = prompts.load_prompt_doc("03-runtime-technical-overview.md")
    assert "vera/automation_preview.py" in text
    assert "vera/automation_lifecycle.py" in text
