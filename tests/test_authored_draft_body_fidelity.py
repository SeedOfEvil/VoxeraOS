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

    def test_inline_heading_split_onto_own_line(self) -> None:
        """Headings appearing mid-line after sentence-ending punctuation must
        be split onto their own line — this was the exact live repro pattern."""
        text = "built into the OS runtime. ### 2. Guarded Execution Lifecycle"
        result = _normalize_markdown_spacing(text)
        assert "runtime. ###" not in result
        assert "### 2. Guarded Execution Lifecycle" in result
        lines = result.split("\n")
        heading_line = next(
            (ln for ln in lines if ln.strip().startswith("### 2.")),
            None,
        )
        assert heading_line is not None, "Heading not found on its own line"

    def test_inline_heading_after_colon(self) -> None:
        text = "Here's the overview: ## Section One"
        result = _normalize_markdown_spacing(text)
        assert "overview: ##" not in result
        lines = result.split("\n")
        assert any(ln.strip().startswith("## Section") for ln in lines)

    def test_inline_heading_after_exclamation(self) -> None:
        text = "That's great! # New Topic"
        result = _normalize_markdown_spacing(text)
        assert "great! #" not in result.replace("\n", " ")

    def test_hash_in_non_heading_context_preserved(self) -> None:
        """C# and other non-heading # uses must not be split."""
        text = "Use the C# language for this."
        result = _normalize_markdown_spacing(text)
        assert "C#" in result
        assert result.count("\n") == text.count("\n")

    def test_zero_space_inline_heading_after_period(self) -> None:
        """Zero space between period and heading: 'safe.### 1.'"""
        text = "auditable, and safe.### 1. The Reasoning Split"
        result = _normalize_markdown_spacing(text)
        assert "safe.###" not in result
        lines = result.split("\n")
        assert any(ln.strip().startswith("### 1.") for ln in lines)

    def test_zero_space_inline_heading_no_punctuation(self) -> None:
        """Heading jammed against preceding word: 'metadata### 4.'"""
        text = "full lineage metadata### 4. Sandboxed Skills"
        result = _normalize_markdown_spacing(text)
        assert "metadata###" not in result
        lines = result.split("\n")
        assert any(ln.strip().startswith("### 4.") for ln in lines)

    def test_single_hash_after_word_not_split(self) -> None:
        """Single # after a word without punctuation is ambiguous — don't split."""
        text = "the queue# Title"
        result = _normalize_markdown_spacing(text)
        # Should NOT be split (single # without preceding punctuation is ambiguous)
        assert result.count("\n") == 0

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

    def test_heading_inline_with_wrapper_now_split(self) -> None:
        """Headings inline with wrapper text are now split onto own lines."""
        reply = (
            "I've prepared a draft explanation. # Execution Safety\n\nAll ops go through the queue."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "queue" in result
        assert "# Execution Safety" in result


class TestInlineHeadingRegression:
    """Regression tests for the live bug where inline headings caused preview
    content corruption.

    The exact live symptom was:
    - ``...OS runtime. ### 2. Guarded Execution Lifecycle``
    - Content truncated after section 2's first bullet
    - Preview content did not match visible drafted reply
    """

    def test_inline_heading_after_sentence_end(self) -> None:
        """Exact live repro pattern: heading mid-line after period."""
        reply = (
            "Here is a note:\n\n"
            "# How VoxeraOS Keeps Execution Safe\n\n"
            "VoxeraOS enforces strict safety through a layered architecture "
            "built into the OS runtime. "
            "### 2. Guarded Execution Lifecycle\n\n"
            "Once a job is approved, it runs inside a sandboxed executor:\n\n"
            "- Skills declare capabilities up front\n"
            "- Execution stays within declared boundaries\n\n"
            "### 3. Evidence-Grounded Outcomes\n\n"
            "Results are surfaced through an evidence layer:\n\n"
            "- Outcomes recorded as auditable artifacts\n\n"
            "This ensures every action is authorized and traceable.\n\n"
            "I've prepared a preview with this content."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None

        # The exact bug: heading collapsed into preceding text
        assert "runtime. ###" not in result, "Inline heading not separated"

        # All sections present
        assert "# How VoxeraOS" in result
        assert "### 2. Guarded Execution" in result
        assert "### 3. Evidence-Grounded" in result

        # No truncation
        assert "authorized and traceable" in result

        # Bullets preserved
        assert "- Skills declare" in result
        assert "- Outcomes recorded" in result

        # Wrapper/trailer removed
        assert "Here is a note" not in result
        assert "prepared a preview" not in result

    def test_multiple_inline_headings(self) -> None:
        """Multiple headings inline with prose on the same line."""
        reply = (
            "# Title\n\n"
            "First section text. ## Second Section\n"
            "Second section body. ### Third Section\n"
            "Third section body.\n\n"
            "I've prepared a preview."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "## Second Section" in result
        assert "### Third Section" in result
        # Headings must be on their own lines
        lines = result.split("\n")
        for line in lines:
            stripped = line.strip()
            if "##" in stripped and not stripped.startswith("#"):
                pytest.fail(f"Heading still inline: {stripped!r}")

    def test_no_content_truncation_with_inline_headings(self) -> None:
        """Content after inline headings must not be truncated."""
        reply = (
            "# Overview\n\n"
            "First section. ## Details\n"
            "Second section has multiple points:\n"
            "- Point A\n"
            "- Point B\n"
            "- Point C\n\n"
            "Final paragraph at the end.\n\n"
            "I've prepared a preview."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "- Point A" in result
        assert "- Point B" in result
        assert "- Point C" in result
        assert "Final paragraph at the end" in result

    @pytest.mark.parametrize(
        "inline_text",
        [
            "End of text. ## Next Section",
            "Some conclusion! ### Subsection Three",
            "Key points: ## Analysis",
            "See above; ## Summary",
            "safe.### 1. The Split",
            "queue### 2. Preview",
            "metadata### 4. Sandboxed",
        ],
    )
    def test_various_punctuation_before_inline_heading(self, inline_text: str) -> None:
        """Headings after various sentence-ending punctuation are split."""
        result = _normalize_markdown_spacing(inline_text)
        lines = result.split("\n")
        heading_lines = [ln for ln in lines if ln.strip().startswith("#")]
        assert len(heading_lines) == 1, f"Expected heading on own line, got: {result!r}"


class TestFiveSectionZeroSpaceExtraction:
    """End-to-end test for a 5-section markdown note where ALL headings are
    inline with zero space — the exact live regression pattern."""

    @pytest.fixture()
    def five_section_zero_space_reply(self) -> str:
        return (
            "I've drafted a short markdown note for you.\n"
            "\n"
            "# How VoxeraOS Keeps Execution Safe\n"
            "VoxeraOS ensures safety through layered boundaries, making everything "
            "transparent, auditable, and safe.### 1. The Reasoning/Execution Split\n"
            "Vera handles interaction but has no execution authority. All execution "
            "flows through the governed queue.### 2. Preview and Explicit Intent\n"
            "Users see a preview before submission:\n"
            "- Goal description\n"
            "- Files to be modified\n"
            "- Execution steps\n"
            "Nothing is submitted without explicit confirmation."
            "### 3. Queue-Based Execution\n"
            "All work enters a persistent queue:\n"
            "- Policy validation\n"
            "- Approval gates\n"
            "- Full lineage metadata"
            "### 4. Sandboxed Skills\n"
            "Jobs execute in bounded sandboxes:\n"
            "- Declared capabilities\n"
            "- Resource limits\n"
            "- Audit trails"
            "### 5. Evidence-Grounded Outcomes\n"
            "Results are surfaced through evidence:\n"
            "- Structured artifacts\n"
            "- Diagnostic context\n"
            "- Grounded success claims\n"
            "This ensures authorized, traceable execution.\n"
            "\n"
            "I've prepared a preview with this content. "
            "This is preview-only \u2014 nothing has been submitted yet."
        )

    def test_all_five_sections_present(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        for i in range(1, 6):
            assert f"### {i}." in result, f"Section {i} missing from extraction"

    def test_no_collapsed_heading_boundaries(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        for i in range(1, 6):
            assert f".### {i}." not in result, f"Section {i} heading collapsed against text"

    def test_headings_on_own_lines(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        lines = result.split("\n")
        for line in lines:
            stripped = line.strip()
            if "###" in stripped and not stripped.startswith("#"):
                pytest.fail(f"Heading still inline: {stripped!r}")

    def test_no_truncation_after_section_2(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        assert "Queue-Based Execution" in result
        assert "Sandboxed Skills" in result
        assert "Evidence-Grounded Outcomes" in result
        assert "traceable execution" in result

    def test_bullets_preserved(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        lines = result.split("\n")
        bullet_count = sum(1 for ln in lines if ln.strip().startswith("- "))
        assert bullet_count >= 9, f"Expected at least 9 bullets, got {bullet_count}"

    def test_wrapper_and_trailer_removed(self, five_section_zero_space_reply: str) -> None:
        result = extract_text_draft_from_reply(five_section_zero_space_reply)
        assert result is not None
        assert "drafted a short" not in result
        assert "prepared a preview" not in result
        assert "preview-only" not in result


# ---------------------------------------------------------------------------
# Regression: wrapper+body extraction for meeting-note live repro
# ---------------------------------------------------------------------------


class TestWrapperBodyExtraction:
    """Regression: "Write me a short note about what happened at the meeting."
    must bind only the drafted body into the preview, not the wrapper line.

    Root cause: _extract_prose_body split only on double-newlines, so
    single-newline and inline wrapper:body formats passed the wrapper
    into the preview content.
    """

    def test_single_newline_wrapper_stripped(self) -> None:
        reply = (
            "Here is a short note summarizing the meeting:\n"
            "The team met on April 4th to discuss Q2. They agreed on revised timelines."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result
        assert "summarizing the meeting" not in result

    def test_inline_colon_wrapper_stripped(self) -> None:
        reply = (
            "Here is a short note summarizing the meeting: "
            "The team met on April 4th to discuss Q2 milestones and agreed on the revised timeline."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result
        assert "summarizing the meeting" not in result

    def test_double_newline_wrapper_stripped(self) -> None:
        reply = (
            "Here is a short note summarizing the meeting:\n\n"
            "The team met on April 4th to discuss Q2. They agreed on revised timelines."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result
        assert "summarizing the meeting" not in result

    def test_wrapper_only_returns_none(self) -> None:
        result = extract_text_draft_from_reply("Here is a short note summarizing the meeting:")
        assert result is None

    def test_no_wrapper_body_survives(self) -> None:
        reply = (
            "The team met on April 4th to discuss Q2 milestones and agreed on the revised timeline."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result

    def test_here_is_note_variant_stripped(self) -> None:
        reply = "Here's the note:\nThe team discussed Q2 goals and agreed on revised timelines."
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result
        assert "Here's the note" not in result

    def test_here_is_summary_variant_stripped(self) -> None:
        reply = (
            "Here is a brief summary of the meeting:\n"
            "The team discussed Q2 goals and everyone agreed on the new timeline."
        )
        result = extract_text_draft_from_reply(reply)
        assert result is not None
        assert "Q2" in result
        assert "summary of the meeting" not in result
