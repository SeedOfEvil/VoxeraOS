"""Speech-only text normalization for TTS input.

Vera assistant replies carry lightweight markdown-style formatting
(headings, bold, inline code, bullets, numbered lists) for the panel
and vera_web UIs.  When that same text is sent to the TTS backend
verbatim, the synthesized speech reads the raw control characters
literally -- ``###`` becomes "hash hash hash", ``**bold**`` becomes
"star star bold star star", bullets become "dash item dash item".

This module provides a single bounded helper,
:func:`normalize_text_for_tts`, that produces a speech-only copy of
assistant text.  The helper is deterministic, conservative, and
purely a string transform -- it never paraphrases, summarizes, or
deletes meaningful content.  It is the only place in the codebase
that rewrites reply text for speech; every canonical TTS entry point
(``synthesize_text`` / ``synthesize_text_async``) routes text through
it so normalization lives in one spot.

Non-negotiables enforced by this module:

* The input string is never mutated in place.
* The returned string is the TTS-only speech copy; callers must
  continue to store / render / log the canonical assistant text
  elsewhere.
* If normalization yields an empty string (e.g. input was purely
  formatting), the original cleaned text is returned so the TTS
  request still has content -- speech quality degrades gracefully
  rather than the reply being silently dropped.

Scope (what *is* normalized):

* ATX heading markers (``#``, ``##``, ``###`` ...).
* Emphasis markers (``**bold**``, ``__bold__``, ``*italic*``,
  ``_italic_``) -- content kept, wrappers removed.
* Inline code backticks -- content kept, backticks removed.
* Triple-backtick fenced code spans on a single line -- fence
  markers removed, code content kept as spoken text.
* Unordered-list bullets (``-``, ``*``, ``+``) at line start.
* Ordered-list prefixes (``1.``, ``2)`` ...) at line start.
* Horizontal rules (``---``, ``***``, ``___``) collapsed out.
* Blockquote markers (``>``) at line start.
* Runs of blank lines collapsed to a single sentence break.

Out of scope (what is *not* changed):

* Sentence wording, order, or semantics.
* URLs, numbers, technical tokens inside normal prose.
* Punctuation beyond formatting-only separators.
"""

from __future__ import annotations

import re

__all__ = ["normalize_text_for_tts"]


# ---------------------------------------------------------------------------
# Regex patterns (compiled once at import time).
# ---------------------------------------------------------------------------
#
# Each pattern is intentionally conservative: it removes only the
# formatting wrapper, never the content inside.  Patterns operate on
# line-level or inline scope; there is no attempt at a full markdown
# parse.

# ATX heading: up to six leading '#' followed by required whitespace,
# anchored to line start after optional leading spaces.
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)

# Horizontal rule: a line consisting entirely of --- / *** / ___ (3+),
# optionally surrounded by whitespace.
_HR_RE = re.compile(r"^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)

# Unordered bullet at line start: '-', '*' or '+' followed by whitespace.
# The leading indentation is preserved-as-space so nested list cadence
# is not lost entirely; only the bullet character is stripped.
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)

# Ordered-list prefix at line start: digits + '.' or ')' + whitespace.
# Like bullets, we keep the indentation but drop the numeric marker so
# a dictated reply does not say "one dot two dot three dot" literally.
# We intentionally keep the leading indentation to preserve cadence.
_ORDERED_RE = re.compile(r"^([ \t]*)\d+[.)][ \t]+", re.MULTILINE)

# Blockquote marker: one or more '>' at line start.
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>+[ \t]?", re.MULTILINE)

# Fenced code block markers (``` or ~~~) on their own line.
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:`{3,}|~{3,})[^\n]*$", re.MULTILINE)

# Bold/strong: **text** or __text__ (non-greedy, no newline inside).
_BOLD_RE = re.compile(r"(?<!\\)\*\*([^*\n]+?)\*\*|(?<!\\)__([^_\n]+?)__")

# Italic/emphasis: *text* or _text_ (non-greedy, no newline inside).
# Intentionally runs after bold so the stronger pattern wins first.
_ITALIC_STAR_RE = re.compile(r"(?<![\\*])\*([^*\n]+?)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<![\\_\w])_([^_\n]+?)_(?![_\w])")

# Inline code: `code`.  We strip the backticks but keep the content.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")

# Collapse runs of 3+ newlines to a single paragraph break.
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# Trailing whitespace per line (cosmetic, but keeps output tidy).
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def _strip_emphasis(text: str) -> str:
    """Remove bold/italic wrappers while keeping their content."""
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    return text


def _strip_inline_code(text: str) -> str:
    """Remove backticks from inline code spans (content preserved)."""
    return _INLINE_CODE_RE.sub(r"\1", text)


def _strip_block_markers(text: str) -> str:
    """Remove line-leading block markers (headings, bullets, quotes)."""
    # Remove horizontal rules and code fences outright -- the text
    # inside a single-line fence is already content; a multi-line
    # fenced block loses only its framing lines here.
    text = _HR_RE.sub("", text)
    text = _FENCE_RE.sub("", text)

    # Strip heading '#' markers but keep the heading words themselves,
    # since the words are the real content the operator wrote.
    text = _HEADING_RE.sub("", text)

    # Bullets and ordered markers -- keep indentation, drop marker.
    text = _BULLET_RE.sub(r"\1", text)
    text = _ORDERED_RE.sub(r"\1", text)

    # Blockquotes: drop the '>' prefix entirely.
    text = _BLOCKQUOTE_RE.sub("", text)
    return text


def _tidy_whitespace(text: str) -> str:
    """Collapse excessive blank runs and trim trailing whitespace."""
    text = _TRAILING_WS_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def normalize_text_for_tts(text: str) -> str:
    """Return a speech-only copy of *text* with formatting syntax removed.

    This helper is the single canonical place where assistant text is
    rewritten for TTS.  It is deterministic and conservative: only
    formatting wrappers are removed, never the underlying words.

    Behavior:

    * ``None`` or empty input -> empty string.
    * Input without any markdown-ish syntax is returned unchanged
      (modulo trimming of surrounding whitespace).
    * If stripping formatting would yield an empty string, the
      original ``text.strip()`` is returned so the TTS pipeline still
      has non-empty content to speak.

    The returned string is **only** suitable for TTS input -- callers
    must not substitute it for canonical assistant text displayed in
    the UI, stored in the session, or persisted to history.
    """
    if not text:
        return ""

    original = str(text)
    working = original

    working = _strip_block_markers(working)
    working = _strip_inline_code(working)
    working = _strip_emphasis(working)
    working = _tidy_whitespace(working)

    if not working:
        # The input was entirely formatting control characters.
        # Returning the stripped original keeps TTS non-empty and
        # truthful rather than silently dropping the reply; the
        # caller still gets something to synthesize.
        return original.strip()

    return working
