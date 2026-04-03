"""Regression tests for authored draft body fidelity in preview content.

These tests protect against preview-content mismatch where the authored
draft body written into write_file.content drifts from the visible
drafted artifact — through truncation, heading collapse, wrapper leakage,
or formatting normalization errors.

Root cause: _extract_prose_body in writing_draft_intent.py splits on
double-newlines only.  When the LLM produces compact markdown (single
newlines between headings and text), wrapper and trailing text leak and
heading spacing collapses.

Fix: _normalize_markdown_spacing inserts blank lines around heading
boundaries before extraction, and trailing wrapper phrases are expanded.
"""

from __future__ import annotations

import pytest

from voxera.core.writing_draft_intent import (
    _normalize_markdown_spacing,
    extract_text_draft_from_reply,
)

# ---------------------------------------------------------------------------
# Unit tests: _normalize_markdown_spacing
# ---------------------------------------------------------------------------


class TestNormalizeMarkdownSpacing:
    """Ensure headings are surrounded by blank lines after normalization."""

    def test_heading_preceded_by_text_gets_blank_line(self) -> None:
        text = "Some text.\n## Heading\nMore text."
        result = _normalize_markdown_spacing(text)
        lines = result.split("\n")
        heading_idx = next(i for i, line in enumerate(lines) if line.startswith("## "))
        assert lines[heading_idx - 1].strip() == ""

    def test_heading_followed_by_text_gets_blank_line(self) -> None:
        text = "## Heading\nBody text follows."
        result = _normalize_markdown_spacing(text)
        lines = result.split("\n")
        heading_idx = next(i for i, line in enumerate(lines) if line.startswith("## "))
        assert lines[heading_idx + 1].strip() == ""

    def test_already_spaced_heading_not_double_spaced(self) -> None:
        text = "Previous.\n\n## Heading\n\nBody."
        result = _normalize_markdown_spacing(text)
        assert "\n\n\n" not in result

    def test_h1_h2_h3_all_normalized(self) -> None:
        text = "Intro.\n# H1\nText.\n## H2\nText.\n### H3\nText."
        result = _normalize_markdown_spacing(text)
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                if i > 0:
                    assert lines[i - 1].strip() == "", f"No blank before {line!r}"
                if i + 1 < len(lines):
                    assert lines[i + 1].strip() == "", f"No blank after {line!r}"

    def test_empty_text_passthrough(self) -> None:
        assert _normalize_markdown_spacing("") == ""

    def test_no_headings_passthrough(self) -> None:
        text = "Just plain text.\nAnother line."
        assert _normalize_markdown_spacing(text) == text

    def test_bullets_not_treated_as_headings(self) -> None:
        text = "- bullet 1\n- bullet 2"
        assert _normalize_markdown_spacing(text) == text


# ---------------------------------------------------------------------------
# Regression tests: authored body fidelity in extract_text_draft_from_reply
# ---------------------------------------------------------------------------


class TestAuthoredBodyFidelity:
    """Regression tests for the live bug where preview content drifted from
    the actual authored draft body.
    """

    @pytest.fixture()
    def compact_markdown_reply(self) -> str:
        return (
            "Here's a short markdown note explaining how VoxeraOS keeps execution safe:\n"
            "\n"
            "# How VoxeraOS Keeps Execution Safe\n"
            "VoxeraOS enforces a strict trust boundary between user intent and system execution.\n"
            "## Queue-Based Execution Boundary\n"
            "All executable work enters a persistent queue:\n"
            "- Jobs are validated against policy\n"
            "- Approval gates block sensitive operations\n"
            "## Sandboxed Skill Execution\n"
            "Each skill runs in a bounded sandbox:\n"
            "- File operations are scoped to safe paths\n"
            "- Network access is controlled per-skill\n"
            "## Evidence-Grounded Results\n"
            "After execution, results are surfaced:\n"
            "- Outcomes are recorded as auditable artifacts\n"
            "- Success claims are grounded in actual evidence\n"
            "This ensures every action is authorized and traceable.\n"
            "\n"
            "I've prepared a preview with this content. "
            "This is preview-only \u2014 nothing has been submitted yet. "
            "Let me know when you'd like to send it."
        )

    @pytest.fixture()
    def well_formatted_reply(self) -> str:
        return (
            "I've drafted a short markdown note for you.\n"
            "\n"
            "# How VoxeraOS Keeps Execution Safe\n"
            "\n"
            "VoxeraOS enforces safety through strict queue boundaries.\n"
            "\n"
            "## Queue-Based Execution\n"
            "\n"
            "All work enters a persistent queue:\n"
            "\n"
            "- Jobs are validated\n"
            "- Approval gates block sensitive ops\n"
            "\n"
            "## Sandboxed Skills\n"
            "\n"
            "Each skill runs in a sandbox:\n"
            "\n"
            "- File ops scoped to safe paths\n"
            "- Resource limits enforced\n"
            "\n"
            "This ensures every action is authorized.\n"
            "\n"
            "I've prepared a preview with this content."
        )

    def test_compact_markdown_headings_preserved(self, compact_markdown_reply: str) -> None:
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        assert "# How VoxeraOS Keeps Execution Safe" in result
        assert "## Queue-Based Execution Boundary" in result
        assert "## Sandboxed Skill Execution" in result
        assert "## Evidence-Grounded Results" in result

    def test_compact_markdown_bullets_preserved(self, compact_markdown_reply: str) -> None:
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        assert "- Jobs are validated against policy" in result
        assert "- File operations are scoped to safe paths" in result
        assert "- Outcomes are recorded as auditable artifacts" in result

    def test_compact_markdown_wrapper_stripped(self, compact_markdown_reply: str) -> None:
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        assert "Here's a short markdown note" not in result

    def test_compact_markdown_trailing_stripped(self, compact_markdown_reply: str) -> None:
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        assert "prepared a preview" not in result
        assert "preview-only" not in result
        assert "nothing has been submitted" not in result

    def test_compact_markdown_no_truncation(self, compact_markdown_reply: str) -> None:
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        assert "authorized and traceable" in result

    def test_compact_markdown_headings_spaced(self, compact_markdown_reply: str) -> None:
        """Headings must be preceded by a blank line for proper markdown rendering."""
        result = extract_text_draft_from_reply(compact_markdown_reply)
        assert result is not None
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("## ") and i > 0:
                assert lines[i - 1].strip() == "", (
                    f"Heading {line.strip()!r} at line {i} not preceded by blank line"
                )

    def test_well_formatted_headings_preserved(self, well_formatted_reply: str) -> None:
        result = extract_text_draft_from_reply(well_formatted_reply)
        assert result is not None
        assert "# How VoxeraOS Keeps Execution Safe" in result
        assert "## Queue-Based Execution" in result
        assert "## Sandboxed Skills" in result

    def test_well_formatted_wrapper_stripped(self, well_formatted_reply: str) -> None:
        result = extract_text_draft_from_reply(well_formatted_reply)
        assert result is not None
        assert "I've drafted" not in result

    def test_well_formatted_trailing_stripped(self, well_formatted_reply: str) -> None:
        result = extract_text_draft_from_reply(well_formatted_reply)
        assert result is not None
        assert "prepared a preview" not in result

    def test_well_formatted_no_truncation(self, well_formatted_reply: str) -> None:
        result = extract_text_draft_from_reply(well_formatted_reply)
        assert result is not None
        assert "every action is authorized" in result


class TestEdgeCases:
    """Edge cases for authored body extraction."""

    def test_no_headings_plain_prose(self) -> None:
        reply = (
            "I've drafted this for you:\n"
            "\n"
            "VoxeraOS keeps execution safe through a layered system of "
            "queue boundaries, sandboxed skills, and evidence-grounded results. "
            "Every action must pass through the queue before any side effects occur."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "queue boundaries" in result

    def test_single_heading_note(self) -> None:
        reply = (
            "Here's the note:\n"
            "\n"
            "# Execution Safety\n"
            "\n"
            "All operations go through the queue. No shortcuts.\n"
            "\n"
            "I've prepared a preview with this content."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "# Execution Safety" in result
        assert "No shortcuts" in result
        assert "prepared a preview" not in result

    def test_heading_inline_with_wrapper(self) -> None:
        """When heading is on same line as wrapper, heading may be lost — this
        is a known limitation, not a regression from this fix."""
        reply = (
            "I've prepared a draft explanation. # Execution Safety\n\nAll ops go through the queue."
        )
        result = extract_text_draft_from_reply(reply)
        # The inline heading case is a known limitation
        assert result is not None
        assert "queue" in result
