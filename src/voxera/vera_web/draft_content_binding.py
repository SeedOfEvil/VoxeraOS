"""Draft content binding: post-LLM reply content extraction and preview binding.

This module owns the pure/mostly-pure derivation logic for extracting draft
content (code and text) from LLM replies and binding it into preview payloads.
It does NOT own final session writes — ``app.py`` retains ownership of all
``write_session_preview`` / ``write_session_handoff_state`` calls.

Extracted from the giant ``chat()`` orchestration function in ``app.py`` to
reduce inline complexity while preserving runtime behavior exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.code_draft_intent import (
    classify_code_draft_intent,
    extract_code_from_reply,
    has_code_file_extension,
)
from ..core.writing_draft_intent import (
    classify_writing_draft_intent,
    extract_text_draft_from_reply,
    is_text_draft_preview,
    is_writing_draft_request,
    is_writing_refinement_request,
)

# ---------------------------------------------------------------------------
# Thin local wrappers (same injection pattern as app.py)
# ---------------------------------------------------------------------------
from ..vera.draft_revision import (  # noqa: E402
    _detect_content_type_from_preview,
    _generate_refreshed_content,
    _is_clear_content_refresh_request,
    is_active_preview_content_expand_request,
    looks_like_preview_rename_or_save_as_request,
)
from ..vera.preview_submission import normalize_preview_payload
from ..vera.saveable_artifacts import (
    looks_like_non_authored_assistant_message,
    message_requests_referenced_content,
)
from .conversational_checklist import (
    has_conversational_planning_signal,
    has_save_write_file_signal,
    looks_like_preview_update_claim,
)
from .execution_mode import (
    _extract_save_as_text_target,
    _message_has_explicit_content_literal,
)
from .execution_mode import (
    _is_governed_writing_preview as _em_is_governed_writing_preview,
)
from .execution_mode import (
    _is_refinable_prose_preview as _em_is_refinable_prose_preview,
)
from .execution_mode import (
    _looks_like_active_preview_content_generation_turn as _em_looks_like_active_preview_content_generation_turn,
)
from .preview_content_binding import looks_like_builder_refinement_placeholder


def _is_refinable_prose_preview(preview: dict[str, object] | None) -> bool:
    return _em_is_refinable_prose_preview(preview, is_text_draft_preview=is_text_draft_preview)


def _is_governed_writing_preview(preview: dict[str, object] | None) -> bool:
    return _em_is_governed_writing_preview(preview, is_text_draft_preview=is_text_draft_preview)


def _looks_like_active_preview_content_generation_turn(message: str) -> bool:
    return _em_looks_like_active_preview_content_generation_turn(
        message,
        looks_like_preview_rename_or_save_as_request=looks_like_preview_rename_or_save_as_request,
        message_requests_referenced_content=message_requests_referenced_content,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def strip_internal_control_blocks(text: str) -> str:
    """Remove internal Voxera control markup from user-visible assistant text."""
    if not text:
        return ""

    cleaned = re.sub(
        r"```[^\n]*\n\s*<voxera_control\b.*?</voxera_control>\s*```",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<voxera_control\b[^>]*>.*?</voxera_control>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Trailing control-prompt stripping
# ---------------------------------------------------------------------------
#
# When the LLM finishes an authored reply with closer prompts like "You can
# check the preview pane for the full list" or "Would you like to submit
# this to be saved, or should we refine it further?", those closers must NOT
# be appended into write_file.content as if they were authored body — they
# are control narration directed at the operator, not file content.
#
# The patterns are anchored to typical Vera-style closer phrases.  Each
# pattern is applied as a paragraph-or-line-final strip so the body of the
# reply is preserved untouched.

_TRAILING_CONTROL_PROMPT_PATTERNS = (
    # Preview pane / draft / file references
    r"(?:^|\n)\s*you\s+can\s+(?:check|see|view|inspect|review|find)\s+"
    r"(?:the\s+)?(?:preview\s+pane|draft|file|note|list|content|full\s+list)"
    r"[^\n]*\.?\s*$",
    # Submit / refine prompts
    r"(?:^|\n)\s*would\s+you\s+like\s+(?:me\s+)?to\s+(?:submit|send|refine|continue|add)"
    r"[^\n]*\??\s*$",
    r"(?:^|\n)\s*should\s+(?:we|i)\s+(?:submit|refine|continue|add|change|update)"
    r"[^\n]*\??\s*$",
    # Acknowledgement / let-me-know closers
    r"(?:^|\n)\s*let\s+me\s+know\s+(?:if|when|whether)"
    r"[^\n]*\.?\s*$",
    r"(?:^|\n)\s*feel\s+free\s+to\s+(?:ask|let\s+me\s+know|tell\s+me)"
    r"[^\n]*\.?\s*$",
    # Preview-only / nothing-submitted closers
    r"(?:^|\n)\s*this\s+is\s+(?:still\s+)?preview-only[^\n]*\.?\s*$",
    r"(?:^|\n)\s*nothing\s+has\s+been\s+submitted[^\n]*\.?\s*$",
    # "I've added/updated/expanded the (preview|draft|list|content|note)"
    # narration that the LLM tacks on top of authored content.  Strip the
    # leading line so authored body is what gets bound.
    r"^\s*i(?:['’]ve|\s+have)?\s+(?:added|appended|updated|expanded|extended)\s+"
    r"(?:\d+\s+(?:more|additional|new|extra|further|another)?\s*"
    r"(?:[a-z][a-z-]*\s+){0,2})?"
    r"(?:to\s+)?(?:the\s+|your\s+)?"
    r"(?:preview|draft|list|content|note|file)[^\n]*\n",
    # "I've added 10 more dad jokes to the list." (one-liner only)
    r"^\s*i(?:['’]ve|\s+have)?\s+added\s+\d+\s+(?:more|additional|new|extra)?\s*"
    r"(?:[a-z][a-z-]*\s+){0,2}"
    r"(?:jokes?|items?|bullets?|examples?|lines?|entries?|points?|facts?|"
    r"stories?|poems?|things?|paragraphs?|sentences?)"
    r"[^\n]*\.?\s*$",
)


def strip_trailing_control_prompts(text: str) -> str:
    """Strip Vera-style control closers from an extracted authored reply.

    Applied iteratively until no further stripping changes the text, so that
    several stacked closers ("You can check the preview pane…\n\nWould you
    like to submit this?") are all removed.

    Pure: takes a string, returns a string.  Whitespace is collapsed at the
    end and the result is trimmed.
    """
    if not text:
        return ""
    cleaned = text
    while True:
        before = cleaned
        for pattern in _TRAILING_CONTROL_PROMPT_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned == before.strip():
            break
    return cleaned


# ---------------------------------------------------------------------------
# Reply draft extraction
# ---------------------------------------------------------------------------


@dataclass
class ReplyDrafts:
    """Extracted draft content from an LLM reply."""

    reply_code_content: str | None
    sanitized_answer: str
    reply_text_draft: str | None


def extract_reply_drafts(
    reply_answer: str,
    message: str,
    *,
    active_preview_is_refinable_prose: bool = False,
) -> ReplyDrafts:
    """Extract code and text draft content from an LLM reply.

    Returns structured extraction results.  Pure — no I/O or session writes.
    """
    reply_code_content = extract_code_from_reply(reply_answer)
    sanitized_answer = strip_internal_control_blocks(reply_answer)
    reply_text_draft_candidate = extract_text_draft_from_reply(sanitized_answer)
    # Writing-draft turns produce authored document content that may legitimately
    # mention system terms (queue state, approval status, etc.).  Skip the
    # non-authored-message filter for these turns so the authored prose is not
    # rejected by a false-positive pattern match.
    # The same bypass applies to refinement turns on active prose previews —
    # "make it shorter" on a note about queue state should not be rejected.
    _is_explicit_writing_turn = is_writing_draft_request(message)
    _is_prose_refinement_turn = (
        active_preview_is_refinable_prose
        and is_writing_refinement_request(message)
        and not looks_like_preview_rename_or_save_as_request(message)
    )
    _bypass_non_authored_filter = _is_explicit_writing_turn or _is_prose_refinement_turn
    reply_text_draft: str | None = (
        None
        if not _bypass_non_authored_filter
        and looks_like_non_authored_assistant_message(str(reply_text_draft_candidate or ""))
        else reply_text_draft_candidate
    )
    if reply_text_draft is None and _looks_like_active_preview_content_generation_turn(message):
        first_block = next(
            (block.strip() for block in re.split(r"\n{2,}", sanitized_answer) if block.strip()),
            "",
        )
        if (
            first_block
            and len(first_block.split()) >= 4
            and not looks_like_non_authored_assistant_message(first_block)
            and not looks_like_preview_update_claim(first_block)
            and not re.search(r"\bprepared\s+(?:a|the)\s+preview\b", first_block, re.IGNORECASE)
        ):
            reply_text_draft = first_block

    # Writing-draft / refinement fallback: when the user explicitly asked to
    # write/draft content (or is refining an active prose preview) and the
    # prose-body extractor returned nothing (e.g. the LLM wrapped the content
    # in a way that _extract_prose_body couldn't parse), use the full
    # sanitized answer as authored content.
    if reply_text_draft is None and _bypass_non_authored_filter:
        candidate = sanitized_answer.strip()
        if candidate and len(candidate.split()) >= 4:
            reply_text_draft = candidate

    return ReplyDrafts(
        reply_code_content=reply_code_content,
        sanitized_answer=sanitized_answer,
        reply_text_draft=reply_text_draft,
    )


# ---------------------------------------------------------------------------
# Draft content binding result
# ---------------------------------------------------------------------------


@dataclass
class DraftContentBindingResult:
    """Result of the post-LLM draft content binding derivation.

    The caller (``app.py``) is responsible for persisting the updated preview
    when ``preview_needs_write`` is ``True``.
    """

    builder_payload: dict[str, object] | None
    is_code_draft_turn: bool
    is_writing_draft_turn: bool
    generation_content_refresh_failed_closed: bool
    preview_needs_write: bool


# ---------------------------------------------------------------------------
# Main binding orchestration
# ---------------------------------------------------------------------------


def resolve_draft_content_binding(  # noqa: C901
    *,
    message: str,
    reply_code_content: str | None,
    reply_text_draft: str | None,
    sanitized_answer: str = "",
    reply_status: str,
    builder_payload: dict[str, object] | None,
    pending_preview: dict[str, object] | None,
    is_code_draft_turn: bool,
    is_writing_draft_turn: bool,
    is_explicit_writing_transform: bool,
    informational_web_turn: bool,
    is_enrichment_turn: bool,
    explicit_targeted_content_refinement: bool,
    active_preview_is_refinable_prose: bool,
    conversational_answer_first_turn: bool,
    active_session: str,
) -> DraftContentBindingResult:
    """Derive draft content binding from LLM reply and existing preview state.

    Runs the full post-LLM content binding pipeline: late code/writing-draft
    detection, code draft injection, writing draft injection, generation
    content binding, content refresh fallback, and create-and-save fallback.

    This is a pure derivation function — it does NOT perform session writes.
    The caller must check ``preview_needs_write`` and persist accordingly.
    """
    generation_content_refresh_failed_closed = False
    preview_needs_write = False

    # ── Late code-draft refinement detection ──
    if (
        not is_code_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and not explicit_targeted_content_refinement
        and isinstance(pending_preview, dict)
        and reply_code_content is not None
    ):
        existing_wf = pending_preview.get("write_file")
        if isinstance(existing_wf, dict) and has_code_file_extension(
            str(existing_wf.get("path") or "")
        ):
            is_code_draft_turn = True

    # ── Code draft injection ──
    if is_code_draft_turn and reply_code_content is not None:
        target_draft: dict[str, object] | None = builder_payload
        builder_has_explicit_content = False
        explicit_literal_content_refinement = bool(
            re.search(
                r"\b("
                r"add\s+content\s+to|"
                r"use\s+this\s+as\s+(?:the\s+)?content|"
                r"(?:content|text)\s*:|"
                r"with\s+(?:the\s+)?(?:content|text)\b|"
                r"as\s+content\s+add|"
                r"put\s+.+?\s+(?:inside|in|into)\s+(?:it|the\s+file)\b"
                r")",
                message,
                re.IGNORECASE,
            )
        )
        if isinstance(builder_payload, dict):
            builder_wf = builder_payload.get("write_file")
            builder_has_explicit_content = isinstance(builder_wf, dict) and bool(
                str(builder_wf.get("content") or "").strip()
            )
        if target_draft is None:
            raw_draft = classify_code_draft_intent(message)
            if raw_draft is not None:
                try:
                    target_draft = normalize_preview_payload(raw_draft)
                except Exception:
                    target_draft = None
        # Fall back to the existing pending preview for refinement turns
        # where the classifier doesn't match but a code preview already exists.
        if target_draft is None and isinstance(pending_preview, dict):
            target_draft = dict(pending_preview)
        if (
            builder_has_explicit_content
            and explicit_literal_content_refinement
            and not isinstance(pending_preview, dict)
        ):
            # When no active code preview exists yet, keep an explicit
            # structured content payload from the deterministic builder rather
            # than replacing it with a speculative fenced-code extraction.
            reply_code_content = None
        if isinstance(target_draft, dict) and reply_code_content is not None:
            wf = target_draft.get("write_file")
            if isinstance(wf, dict):
                updated_draft: dict[str, object] = {
                    **target_draft,
                    "write_file": {**wf, "content": reply_code_content},
                }
                builder_payload = updated_draft
                preview_needs_write = True

    # ── Late writing-draft refinement detection ──
    is_existing_refinable_prose_preview = active_preview_is_refinable_prose
    is_existing_writing_preview = _is_governed_writing_preview(pending_preview)
    if (
        not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and not explicit_targeted_content_refinement
        and is_existing_refinable_prose_preview
        and is_writing_refinement_request(message)
        and reply_text_draft is not None
    ):
        is_writing_draft_turn = True

    # ── Writing draft context flags ──
    pending_preview_write_file = (
        pending_preview.get("write_file") if isinstance(pending_preview, dict) else None
    )
    pending_preview_path = (
        str(pending_preview_write_file.get("path") or "").strip()
        if isinstance(pending_preview_write_file, dict)
        else ""
    )
    pending_preview_content = (
        str(pending_preview_write_file.get("content") or "").strip()
        if isinstance(pending_preview_write_file, dict)
        else ""
    )
    active_preview_is_code = (
        bool(pending_preview_path)
        and has_code_file_extension(pending_preview_path)
        and not pending_preview_path.lower().endswith(".md")
    )
    builder_is_governed_writing_preview = _is_governed_writing_preview(builder_payload)
    builder_preview_write_file = (
        builder_payload.get("write_file") if isinstance(builder_payload, dict) else None
    )
    builder_preview_path = (
        str(builder_preview_write_file.get("path") or "").strip()
        if isinstance(builder_preview_write_file, dict)
        else ""
    )
    builder_preview_is_code = (
        bool(builder_preview_path)
        and has_code_file_extension(builder_preview_path)
        and not builder_preview_path.lower().endswith(".md")
    )

    should_preserve_builder_refinement_content = (
        active_preview_is_code
        and not builder_is_governed_writing_preview
        and (not builder_preview_path or builder_preview_is_code)
    )
    if (
        not should_preserve_builder_refinement_content
        and isinstance(builder_payload, dict)
        and is_writing_refinement_request(message)
    ):
        builder_wf = builder_payload.get("write_file")
        builder_content = (
            str(builder_wf.get("content") or "").strip() if isinstance(builder_wf, dict) else ""
        )
        reply_text_draft_content = str(reply_text_draft or "").strip()
        should_preserve_builder_refinement_content = (
            not is_existing_writing_preview
            and bool(builder_content)
            and builder_content != pending_preview_content
            and (not reply_text_draft_content or builder_content == reply_text_draft_content)
            and not looks_like_builder_refinement_placeholder(builder_content)
        )

    # ── Writing draft injection ──
    if (
        is_writing_draft_turn
        and reply_text_draft is not None
        and not reply_status.startswith("degraded")
        and not should_preserve_builder_refinement_content
    ):
        prose_target_draft: dict[str, object] | None = builder_payload
        if prose_target_draft is None:
            raw_draft = classify_writing_draft_intent(message)
            if raw_draft is not None:
                try:
                    prose_target_draft = normalize_preview_payload(raw_draft)
                except Exception:
                    prose_target_draft = None
        if (
            prose_target_draft is None
            and isinstance(pending_preview, dict)
            and _is_refinable_prose_preview(pending_preview)
        ):
            prose_target_draft = dict(pending_preview)
        if isinstance(prose_target_draft, dict):
            wf = prose_target_draft.get("write_file")
            if isinstance(wf, dict):
                updated_prose_draft: dict[str, object] = {
                    **prose_target_draft,
                    "write_file": {**wf, "content": reply_text_draft},
                }
                builder_payload = updated_prose_draft
                preview_needs_write = True

    # ── Single-turn generate+save: bind reply text to empty content shell ──
    if (
        isinstance(builder_payload, dict)
        and _is_refinable_prose_preview(builder_payload)
        and not is_code_draft_turn
        and not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and _looks_like_active_preview_content_generation_turn(message)
        and reply_text_draft is not None
        and not str(reply_status).strip().lower().startswith("degraded")
    ):
        _shell_wf = builder_payload.get("write_file")
        _shell_content = (
            str(_shell_wf.get("content") or "").strip() if isinstance(_shell_wf, dict) else ""
        )
        if isinstance(_shell_wf, dict) and not _shell_content:
            shell_bound_preview: dict[str, object] = {
                **builder_payload,
                "write_file": {**_shell_wf, "content": reply_text_draft},
            }
            builder_payload = shell_bound_preview
            preview_needs_write = True

    # ── Active-preview content append / expand binding ──
    # Handles additive follow-ups like "add 10 more jokes to the list",
    # "append 3 more examples", "continue the list".  When the LLM reply
    # produced authored prose, append it to the existing preview content and
    # update the active preview.  When the LLM produced no authored content,
    # fail closed so response shaping can surface an honest "draft unchanged"
    # reply instead of letting the LLM's "I've added N jokes" claim leak.
    _is_expand_request = is_active_preview_content_expand_request(message)
    if (
        _is_expand_request
        and not is_code_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and isinstance(pending_preview, dict)
        and _is_refinable_prose_preview(pending_preview)
        and not str(reply_status).strip().lower().startswith("degraded")
        and not isinstance(builder_payload, dict)
    ):
        _existing_wf = pending_preview.get("write_file")
        _existing_content = (
            str(_existing_wf.get("content") or "") if isinstance(_existing_wf, dict) else ""
        )
        # Strip trailing control prompts ("You can check the preview pane…",
        # "Would you like to submit this?", "I've added 10 more dad jokes…")
        # so the LLM's closer narration never lands as authored content.
        _new_addition = strip_trailing_control_prompts((reply_text_draft or "").strip()).strip()
        if isinstance(_existing_wf, dict) and _existing_content.strip() and _new_addition:
            # Sanity 1: reject wrapper / status / control narration so it
            # never lands in file content as authored body.
            # Sanity 2: reject LLM replies that are PURE preview-update
            # claims ("I've added 10 more dad jokes to the list.") with no
            # actual additional authored content — appending such a reply
            # would write the false claim verbatim into the saved file.
            _looks_like_pure_claim = looks_like_preview_update_claim(_new_addition) and (
                len(_new_addition.split()) < 30
            )
            if looks_like_non_authored_assistant_message(_new_addition) or _looks_like_pure_claim:
                generation_content_refresh_failed_closed = True
            else:
                # Best-effort dedupe: if the LLM replied with the full new
                # content (existing body + additions), detect that and use it
                # as a REPLACE instead of doubling via APPEND.
                _existing_head = _existing_content.strip()[:60].lower()
                if _existing_head and _existing_head in _new_addition.lower()[:200]:
                    combined_content = _new_addition
                else:
                    combined_content = _existing_content.rstrip() + "\n" + _new_addition
                # Truth guard: only commit a builder_payload when the
                # combined content actually differs from existing content.
                # Without this, the conversational "updated the preview"
                # reply could fire when the binding produced a no-op
                # change (e.g. all the LLM additions were stripped as
                # control narration).
                if combined_content.strip() != _existing_content.strip():
                    appended_preview: dict[str, object] = {
                        **pending_preview,
                        "write_file": {**_existing_wf, "content": combined_content},
                    }
                    builder_payload = appended_preview
                    preview_needs_write = True
                else:
                    generation_content_refresh_failed_closed = True
        elif isinstance(_existing_wf, dict) and not _new_addition:
            # LLM produced no authored text to append (or only stripped
            # control narration) — fail closed so the assistant reply
            # cannot overclaim an update.
            generation_content_refresh_failed_closed = True

    # ── Generation content binding ──
    generation_binding_intent = (
        not is_code_draft_turn
        and not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and _looks_like_active_preview_content_generation_turn(message)
        and not _message_has_explicit_content_literal(message)
        and not str(reply_status).strip().lower().startswith("degraded")
    )
    if generation_binding_intent and (
        _is_refinable_prose_preview(builder_payload) or _is_refinable_prose_preview(pending_preview)
    ):
        if reply_text_draft is not None:
            target_preview = (
                builder_payload
                if _is_refinable_prose_preview(builder_payload)
                else pending_preview
                if _is_refinable_prose_preview(pending_preview)
                else None
            )
            if isinstance(target_preview, dict):
                target_wf = target_preview.get("write_file")
                if isinstance(target_wf, dict):
                    save_as_target = _extract_save_as_text_target(message)
                    rewritten_path = str(target_wf.get("path") or "").strip()
                    if save_as_target:
                        rewritten_path = f"~/VoxeraOS/notes/{save_as_target}"
                    updated_preview: dict[str, object] = {
                        **target_preview,
                        "write_file": {
                            **target_wf,
                            "path": rewritten_path or target_wf.get("path"),
                            "content": reply_text_draft,
                        },
                    }
                    builder_payload = updated_preview
                    preview_needs_write = True
        else:
            # Deterministic active-draft content refresh fallback: when the LLM
            # did not produce usable text but the user clearly asked for a content
            # refresh (e.g. "generate a different poem"), generate replacement
            # content deterministically from a content-type pool.
            _refresh_target = (
                builder_payload
                if _is_refinable_prose_preview(builder_payload)
                else pending_preview
                if _is_refinable_prose_preview(pending_preview)
                else None
            )
            if isinstance(_refresh_target, dict) and _is_clear_content_refresh_request(
                message.strip().lower()
            ):
                _refresh_wf = _refresh_target.get("write_file")
                if isinstance(_refresh_wf, dict):
                    _refresh_path = str(_refresh_wf.get("path") or "").strip()
                    _existing = str(_refresh_wf.get("content") or "")
                    _ctype = _detect_content_type_from_preview(
                        _refresh_target, message.strip().lower()
                    )
                    _refreshed = _generate_refreshed_content(_ctype, _existing)
                    if _refreshed and _refreshed != _existing.strip():
                        _refreshed_preview: dict[str, object] = {
                            **_refresh_target,
                            "write_file": {
                                **_refresh_wf,
                                "content": _refreshed,
                            },
                        }
                        builder_payload = _refreshed_preview
                        preview_needs_write = True
                    else:
                        generation_content_refresh_failed_closed = True
                else:
                    generation_content_refresh_failed_closed = True
            else:
                generation_content_refresh_failed_closed = True

    # ── Create-and-save fallback ──
    _is_create_and_save = (
        not conversational_answer_first_turn
        and not is_writing_draft_turn
        and not is_code_draft_turn
        and builder_payload is None
        and pending_preview is None
        and has_save_write_file_signal(message)
        and has_conversational_planning_signal(message)
    )
    if _is_create_and_save and reply_text_draft:
        _note_suffix = active_session[-8:] if len(active_session) >= 8 else active_session
        _create_save_payload: dict[str, object] = {
            "goal": f"save checklist/plan to a note ({message[:60]})",
            "write_file": {
                "path": f"~/VoxeraOS/notes/note-{_note_suffix}.md",
                "content": reply_text_draft,
                "mode": "overwrite",
            },
        }
        try:
            builder_payload = normalize_preview_payload(_create_save_payload)
            preview_needs_write = True
        except Exception:
            builder_payload = None

    # ── Writing-draft preview truth guardrail ──
    # When the user explicitly asked for a writing draft, verify that the final
    # preview write_file.content is not a short fragment from the builder.
    # This closes the gap where a pathological builder response produces a
    # snippet (e.g. a mid-sentence fragment) that survives into the
    # authoritative preview payload.
    #
    # The guardrail uses the best available authored content source:
    # 1. reply_text_draft (extracted prose body) — preferred
    # 2. sanitized_answer (full LLM reply, stripped of control blocks) —
    #    fallback when extraction missed but the LLM produced good content
    _guardrail_content = reply_text_draft
    if _guardrail_content is None or len(_guardrail_content.split()) < 8:
        _sa_candidate = sanitized_answer.strip()
        if _sa_candidate and len(_sa_candidate.split()) >= 8:
            _guardrail_content = _sa_candidate

    if (
        is_writing_draft_turn
        and _guardrail_content is not None
        and len(_guardrail_content.split()) >= 8
        and isinstance(builder_payload, dict)
    ):
        _final_wf = builder_payload.get("write_file")
        _final_content = (
            str(_final_wf.get("content") or "").strip() if isinstance(_final_wf, dict) else ""
        )
        if (
            isinstance(_final_wf, dict)
            and len(_final_content.split()) < len(_guardrail_content.split()) // 2
            and _final_content != _guardrail_content.strip()
        ):
            builder_payload = {
                **builder_payload,
                "write_file": {**_final_wf, "content": _guardrail_content},
            }
            preview_needs_write = True

    return DraftContentBindingResult(
        builder_payload=builder_payload,
        is_code_draft_turn=is_code_draft_turn,
        is_writing_draft_turn=is_writing_draft_turn,
        generation_content_refresh_failed_closed=generation_content_refresh_failed_closed,
        preview_needs_write=preview_needs_write,
    )
