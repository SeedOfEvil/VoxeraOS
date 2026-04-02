"""Response shaping: post-guardrail assistant reply assembly.

This module owns the pure / mostly-pure derivation logic for assembling the
final assistant reply text after guardrails have been applied.  It does NOT
perform session reads or writes — all I/O and session persistence stay in
``app.py``.

Extracted from the giant ``chat()`` orchestration function in ``app.py`` to
reduce inline complexity while preserving runtime behavior exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.code_draft_intent import has_code_file_extension
from ..vera.draft_revision import (
    _is_ambiguous_change_request,
    looks_like_preview_rename_or_save_as_request,
)
from ..vera.handoff import is_recent_assistant_content_save_request
from .conversational_checklist import (
    conversational_preview_update_message as _cc_conversational_preview_update_message,
)
from .conversational_checklist import (
    looks_like_preview_pane_claim as _cc_looks_like_preview_pane_claim,
)
from .conversational_checklist import (
    looks_like_preview_update_claim,
    looks_like_voxera_preview_dump,
)
from .execution_mode import (
    _looks_like_ambiguous_active_preview_content_replacement_request,
)

# ---------------------------------------------------------------------------
# Thin local wrapper (same injection pattern as draft_content_binding.py)
# ---------------------------------------------------------------------------


def _conversational_preview_update_message(
    *,
    updated: bool,
    has_active_preview: bool,
    user_message: str,
    rejected: bool = False,
    updated_preview: dict[str, object] | None = None,
    preview_already_existed: bool = False,
) -> str:
    return _cc_conversational_preview_update_message(
        updated=updated,
        has_active_preview=has_active_preview,
        user_message=user_message,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request(
            user_message
        ),
        rejected=rejected,
        updated_preview=updated_preview,
        preview_already_existed=preview_already_existed,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def derive_preview_has_content(effective_preview: dict[str, object] | None) -> bool:
    """Return True when ``effective_preview`` contains real authored content.

    An empty-content ``write_file`` preview is a placeholder, not authoritative
    content — this returns False for such shells so preview-existence claims are
    gated on actual content, not a metadata-only skeleton.
    """
    if not isinstance(effective_preview, dict):
        return False
    epwf = effective_preview.get("write_file")
    if isinstance(epwf, dict):
        preview_path = str(epwf.get("path") or "").strip()
        preview_content = str(epwf.get("content") or "").strip()
        return bool(preview_content) or (
            bool(preview_path) and not has_code_file_extension(preview_path)
        )
    # write_file key absent → non-write_file job payload is a real preview.
    # write_file key present but non-dict → treat as no authoritative content.
    return "write_file" not in effective_preview


def guardrail_false_preview_claim(text: str, *, preview_exists: bool) -> str:
    """Replace false preview-existence claims with truthful language.

    When the LLM claims a preview/draft was created or is available but no
    authoritative preview state exists, replace the claim.  Fenced code
    blocks are preserved so users can still see generated code.
    """
    if preview_exists:
        return text
    if not _cc_looks_like_preview_pane_claim(text):
        return text

    # Preserve any fenced code blocks
    code_blocks = re.findall(r"```[^\n]*\n.*?```", text, flags=re.DOTALL)
    if code_blocks:
        preserved = "\n\n".join(code_blocks)
        return (
            preserved
            + "\n\n"
            + "Note: I was not able to create a governed preview for this code. "
            + "The code above is shown for reference only — "
            + "no preview is active in this session."
        )
    return (
        "I was not able to prepare a governed preview for this request. "
        "If you share clearer details, I can try again."
    )


def should_clear_stale_preview(
    guarded_answer: str,
    answer_before_guardrail: str,
    effective_preview: dict[str, object] | None,
) -> bool:
    """Return True when a stale empty ``write_file`` shell should be cleared.

    When ``guardrail_false_preview_claim`` strips a false preview-existence
    claim, any orphaned empty ``write_file`` placeholder should be cleared to
    keep the session clean — no orphaned shell, no accidental empty submission.

    Pure — no I/O.  The caller (``app.py``) owns the ``write_session_preview``
    call when this returns True.
    """
    if guarded_answer == answer_before_guardrail:
        return False
    if not isinstance(effective_preview, dict):
        return False
    stale_wf = effective_preview.get("write_file")
    return isinstance(stale_wf, dict) and not str(stale_wf.get("content") or "").strip()


# ---------------------------------------------------------------------------
# Reply assembly
# ---------------------------------------------------------------------------


@dataclass
class AssistantReplyResult:
    """Result of the post-guardrail assistant reply assembly.

    The caller (``app.py``) is responsible for appending the turn and
    rendering the page via ``append_session_turn`` and ``_render_page``.
    """

    assistant_text: str
    status: str


def assemble_assistant_reply(  # noqa: C901
    guarded_answer: str,
    *,
    message: str,
    pending_preview: dict[str, object] | None,
    builder_payload: dict[str, object] | None,
    in_voxera_preview_flow: bool,
    is_code_draft_turn: bool,
    is_writing_draft_turn: bool,
    is_enrichment_turn: bool,
    conversational_answer_first_turn: bool,
    is_json_content_request: bool,
    is_voxera_control_turn: bool,
    explicit_targeted_content_refinement: bool,
    preview_update_rejected: bool,
    generation_content_refresh_failed_closed: bool,
    reply_status: str,
) -> AssistantReplyResult:
    """Assemble the final assistant reply text and status string.

    Selects the appropriate assistant-facing text from the post-guardrail
    answer and the current preview/draft/turn state.  Pure derivation — no
    I/O or session writes.  The caller (``app.py``) owns ``append_session_turn``
    and the final ``_render_page`` call.
    """
    should_hide_voxera_preview_dump = (
        in_voxera_preview_flow or is_voxera_control_turn
    ) and not is_json_content_request

    assistant_text = guarded_answer
    naming_mutation_request = looks_like_preview_rename_or_save_as_request(message)
    # Writing draft turns generate new authored content — do not override the LLM
    # reply with a path-mutation message even when the message contains "save as".
    if (
        naming_mutation_request
        and not is_writing_draft_turn
        and (pending_preview is not None or builder_payload is not None)
    ):
        assistant_text = _conversational_preview_update_message(
            updated=builder_payload is not None,
            has_active_preview=pending_preview is not None,
            user_message=message,
            rejected=preview_update_rejected,
            updated_preview=builder_payload,
            preview_already_existed=pending_preview is not None,
        )
    if (
        explicit_targeted_content_refinement
        and builder_payload is not None
        and not is_code_draft_turn
        and not is_writing_draft_turn
    ):
        assistant_text = _conversational_preview_update_message(
            updated=True,
            has_active_preview=pending_preview is not None,
            user_message=message,
            updated_preview=builder_payload,
            preview_already_existed=pending_preview is not None,
        )
    # Code draft replies must NOT be suppressed — they contain the actual code
    # that the user needs to see in a proper fenced block.  All other preview
    # control-turn suppression logic still applies.
    should_use_conversational_control_reply = (
        not is_enrichment_turn
        and not is_code_draft_turn
        and not is_writing_draft_turn
        and not conversational_answer_first_turn
        and (
            (is_voxera_control_turn and not is_json_content_request)
            or (should_hide_voxera_preview_dump and looks_like_voxera_preview_dump(guarded_answer))
            or (looks_like_preview_update_claim(guarded_answer) and not is_json_content_request)
        )
    )
    if should_use_conversational_control_reply or not assistant_text.strip():
        assistant_text = _conversational_preview_update_message(
            updated=builder_payload is not None,
            has_active_preview=pending_preview is not None,
            user_message=message,
            rejected=preview_update_rejected,
            updated_preview=builder_payload,
            preview_already_existed=pending_preview is not None,
        )
    if (
        builder_payload is None
        and isinstance(pending_preview, dict)
        and _looks_like_ambiguous_active_preview_content_replacement_request(message)
    ):
        assistant_text = (
            f"{assistant_text}\n\n"
            "I left the active draft content unchanged because the content replacement request was "
            "ambiguous. Please specify exact content or say what prior artifact to use."
        ).strip()
    if (
        builder_payload is None
        and isinstance(pending_preview, dict)
        and _is_ambiguous_change_request(message.strip().lower())
    ):
        assistant_text = (
            "I left the active draft content unchanged because the request was ambiguous. "
            "To refresh content, try something specific like 'generate a different poem' "
            "or 'tell me a different joke'."
        )
    if generation_content_refresh_failed_closed:
        assistant_text = (
            f"{assistant_text}\n\n"
            "I left the active draft content unchanged because I could not use this turn as "
            "authoritative generated content. Please ask for explicit content again or provide "
            "the exact text to save."
        ).strip()

    status = "prepared_preview" if builder_payload is not None else reply_status

    return AssistantReplyResult(assistant_text=assistant_text, status=status)
