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
from ..vera.preview_drafting import is_recent_assistant_content_save_request
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


def _looks_like_internal_compiler_payload(block: str) -> bool:
    """Return True when a fenced code block looks like an internal compiler/JSON payload.

    Internal payloads typically contain structured keys like intent, reasoning,
    decisions, write_file, or tool — these must never leak into visible chat.
    """
    lowered = block.lower()
    internal_markers = (
        '"intent"',
        '"reasoning"',
        '"decisions"',
        '"write_file"',
        '"tool"',
        '"action"',
        '"enqueue_child"',
    )
    marker_count = sum(1 for m in internal_markers if m in lowered)
    return marker_count >= 2


BLANKET_PREVIEW_REFUSAL_TEXT = (
    "I was not able to prepare a governed preview for this request. "
    "If you share clearer details, I can try again."
)


def guardrail_false_preview_claim(text: str, *, preview_exists: bool) -> str:
    """Replace false preview-existence claims with truthful language.

    When the LLM claims a preview/draft was created or is available but no
    authoritative preview state exists, replace the claim.  Fenced code
    blocks are preserved so users can still see generated code — unless they
    contain internal compiler/JSON payloads, which are always stripped.
    """
    if preview_exists:
        return text
    if not _cc_looks_like_preview_pane_claim(text):
        return text

    # Preserve user-facing fenced code blocks but strip internal compiler payloads
    code_blocks = re.findall(r"```[^\n]*\n.*?```", text, flags=re.DOTALL)
    user_facing_blocks = [b for b in code_blocks if not _looks_like_internal_compiler_payload(b)]
    if user_facing_blocks:
        preserved = "\n\n".join(user_facing_blocks)
        return (
            preserved
            + "\n\n"
            + "Note: I was not able to create a governed preview for this code. "
            + "The code above is shown for reference only — "
            + "no preview is active in this session."
        )
    return BLANKET_PREVIEW_REFUSAL_TEXT


_BARE_JSON_INTERNAL_MARKERS = (
    '"intent"',
    '"reasoning"',
    '"decisions"',
    '"write_file"',
    '"tool"',
    '"action"',
)


def _strip_bare_internal_json_objects(text: str) -> str:
    """Strip bare JSON objects containing internal compiler markers.

    Handles nested braces correctly by tracking brace depth rather than
    relying on a single regex, which would leave trailing ``}`` residue
    for payloads like ``{"intent":"x","write_file":{"path":"y"}}``.

    For multi-line bare JSON, peeks ahead through the block to count markers
    across all lines before deciding to strip.
    """
    lines = text.split("\n")
    out: list[str] = []
    skip_depth = 0
    for idx, line in enumerate(lines):
        if skip_depth > 0:
            skip_depth += line.count("{") - line.count("}")
            continue
        stripped = line.lstrip()
        if stripped.startswith("{"):
            # Peek ahead: gather the full JSON block text to check markers
            peek_text = stripped.lower()
            depth = stripped.count("{") - stripped.count("}")
            if depth > 0:
                for future_line in lines[idx + 1 :]:
                    peek_text += " " + future_line.lower()
                    depth += future_line.count("{") - future_line.count("}")
                    if depth <= 0:
                        break
            marker_count = sum(1 for m in _BARE_JSON_INTERNAL_MARKERS if m in peek_text)
            if marker_count >= 2:
                block_depth = stripped.count("{") - stripped.count("}")
                if block_depth <= 0:
                    # Single-line balanced JSON — skip it entirely
                    continue
                skip_depth = block_depth
                continue
        out.append(line)
    return "\n".join(out)


def strip_internal_compiler_leakage(text: str) -> str:
    """Strip internal compiler/JSON payloads from assistant-visible text.

    Catches fenced code blocks and bare JSON objects containing internal
    compiler markers (intent, reasoning, decisions, write_file, tool).
    This is a defense-in-depth guardrail for GOVERNED_PREVIEW mode where
    the nuclear conversational sanitizer does not run.
    """
    if not text or not text.strip():
        return text

    # Strip fenced code blocks that look like internal compiler payloads
    def _replace_if_internal(match: re.Match[str]) -> str:
        return "" if _looks_like_internal_compiler_payload(match.group(0)) else match.group(0)

    cleaned = re.sub(r"```[^\n]*\n.*?```", _replace_if_internal, text, flags=re.DOTALL)

    # Strip bare JSON objects that look like internal payloads.
    # Uses brace-depth tracking to handle nested objects correctly.
    cleaned = _strip_bare_internal_json_objects(cleaned)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    # If stripping removed all content, the entire text was internal payload —
    # return empty rather than falling back to the original leaked content.
    return cleaned


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
# LLM preview-narration stripping
# ---------------------------------------------------------------------------

_LLM_PREVIEW_NARRATION_RE = re.compile(
    r"(?:^|\n\n)"  # paragraph boundary
    r"(?:I(?:\u2019ve|\u0027ve|'ve| have| already)?\s+"
    r"(?:updated|prepared|created|set up|drafted|placed|put|saved)"
    r".*?"
    r"(?:preview|draft|note|file|pane|submit|submitted|changes)"
    r".*?"
    r"[.!])"
    r"(?:\s+(?:You can|This is|Let me|Nothing|Feel free|Review).*?[.!])*",
    re.IGNORECASE | re.DOTALL,
)


def _strip_llm_preview_narration(text: str) -> str:
    """Strip trailing LLM-generated preview/draft update narration.

    When the LLM produces authored content followed by narration like
    "I've updated the draft to include this note. You can review…",
    strip that narration so the canonical preview-state notice can be
    appended without duplication.

    Only strips when the remaining text still has real content (at least
    4 words). If the entire text is narration, it is left as-is so the
    stock notice can replace it.
    """
    cleaned = _LLM_PREVIEW_NARRATION_RE.sub("", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    # Only accept the stripped version if real content remains
    if cleaned and len(cleaned.split()) >= 4:
        return cleaned
    return text.strip()


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

    # Writing-draft turns show authored content in chat (not a control message).
    # When a preview was prepared or updated on such a turn, append a
    # preview-state notice so the user clearly knows preview state.
    #
    # When the LLM reply already contains preview/draft narration (e.g.
    # "I've updated the draft to include this note…"), strip that narration
    # before appending the canonical preview-state notice.  This prevents
    # triple-layered responses (content + LLM narration + stock narration).
    if is_writing_draft_turn and builder_payload is not None and assistant_text.strip():
        assistant_text = _strip_llm_preview_narration(assistant_text)
        if pending_preview is not None:
            assistant_text = (
                f"{assistant_text}\n\n"
                "I\u2019ve updated the preview with your changes. "
                "This is still preview-only \u2014 nothing has been submitted yet."
            )
        else:
            assistant_text = (
                f"{assistant_text}\n\n"
                "I\u2019ve prepared a preview with this content. "
                "This is preview-only \u2014 nothing has been submitted yet. "
                "Let me know when you\u2019d like to send it."
            )

    status = "prepared_preview" if builder_payload is not None else reply_status

    return AssistantReplyResult(assistant_text=assistant_text, status=status)
