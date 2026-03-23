from __future__ import annotations

from voxera.vera.investigation_derivations import (
    derive_investigation_comparison,
    derive_investigation_expansion,
    draft_investigation_derived_save_preview,
)

from .vera_session_helpers import sample_investigation_payload


def test_compare_derivation_selects_requested_results_only() -> None:
    derived = derive_investigation_comparison(
        "compare results 1 and 3",
        investigation_context=sample_investigation_payload(),
    )

    assert derived is not None
    assert derived["derivation_type"] == "comparison"
    assert derived["selected_result_ids"] == [1, 3]
    assert "Compared results: 1, 3" in derived["answer"]
    assert "# Investigation Comparison" in derived["markdown"]


def test_expand_derivation_preserves_result_metadata_in_markdown() -> None:
    derived = derive_investigation_expansion(
        "expand result 1",
        investigation_context=sample_investigation_payload(),
        expanded_text=(
            "Result 1 expands into a practical incident-response workflow with triage, "
            "containment, and human review."
        ),
    )

    assert derived is not None
    assert derived["derivation_type"] == "expanded_result"
    assert derived["selected_result_ids"] == [1]
    assert derived["result_title"] == "Guide A"
    assert "## Result Metadata" in derived["markdown"]
    assert "## Expanded Writeup" in derived["markdown"]


def test_derived_save_preview_uses_authoritative_derived_markdown() -> None:
    preview = draft_investigation_derived_save_preview(
        "save that to a note",
        derived_output={
            "derivation_type": "summary",
            "markdown": "# Investigation Summary\n\nBody\n",
        },
    )

    assert preview is not None
    assert preview["goal"] == "write investigation summary to markdown note"
    assert preview["write_file"]["content"] == "# Investigation Summary\n\nBody\n"
