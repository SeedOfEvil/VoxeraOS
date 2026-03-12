from pathlib import Path

import pytest

from voxera import prompts
from voxera.core.planner_context import get_planner_preamble
from voxera.vera import prompt as vera_prompt


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


def test_all_prompt_docs_exist_for_declared_roles() -> None:
    root = Path("docs/prompts")
    for role, role_doc in prompts._ROLE_DOCS.items():  # noqa: SLF001
        assert (root / role_doc).exists(), role
        for capability_doc in prompts._ROLE_CAPABILITY_DOCS[role]:  # noqa: SLF001
            assert (root / capability_doc).exists(), f"{role}: {capability_doc}"
