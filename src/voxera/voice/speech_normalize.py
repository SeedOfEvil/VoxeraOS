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
* If the input was entirely formatting characters (e.g. ``"###"`` or
  ``"---"`` on their own), the helper returns an empty string rather
  than the raw punctuation.  The downstream ``build_tts_request``
  then raises ``ValueError`` for empty text, which every canonical
  caller already catches fail-soft -- the reply stays text-
  authoritative and the synthesizer never gets asked to speak
  "hash hash hash" or "dash dash dash" literally.

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
* Blockquote markers (``>``) at line start, including nested
  ``> > quote`` on the same line.
* Bare runs of formatting characters on their own line
  (``###``, ``---``, ``**`` ...) collapsed to silence.
* Runs of blank lines collapsed to a single sentence break.

Out of scope (what is *not* changed):

* Sentence wording, order, or semantics.
* URLs, numbers, technical tokens inside normal prose.
* Punctuation beyond formatting-only separators.
* Unmatched asterisk runs (``"Orphan *** alone"``) are left as-is
  because there is no valid bold/italic span to unwrap; the helper
  never invents structure.  Fully-nested ``***bold italic***``
  *does* normalize cleanly by construction (bold strips first,
  italic then strips the residual single wrapper).
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

# Blockquote marker: one or more '>' at line start, including nested
# same-line quotes like ``> > nested`` which we want to strip in a
# single pass so the inner '>' never reaches the synthesizer.
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*(?:>+[ \t]*)+", re.MULTILINE)

# Fenced code block markers (``` or ~~~) on their own line.
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:`{3,}|~{3,})[^\n]*$", re.MULTILINE)

# Bold/strong: **text** or __text__ (non-greedy, no newline inside,
# no whitespace adjacent to the delimiters).  The ``(?!\s)`` / ``(?<!\s)``
# guards match the CommonMark rule that emphasis delimiters cannot sit
# next to whitespace -- this protects arithmetic expressions like
# ``2 ** 3`` from being misread as bold wrappers.
_BOLD_RE = re.compile(r"(?<!\\)\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*|(?<!\\)__(?!\s)([^_\n]+?)(?<!\s)__")

# Italic/emphasis: *text* or _text_ (non-greedy, no newline inside,
# no whitespace adjacent to the delimiters).  Matches CommonMark rules
# so expressions like ``2 * 3 * 4`` and ``glob * pattern`` are left
# untouched instead of being stripped as italic wrappers.
# Intentionally runs after bold so the stronger pattern wins first.
_ITALIC_STAR_RE = re.compile(r"(?<![\\*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<![\\_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")

# Inline code: `code`.  We strip the backticks but keep the content.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")

# Collapse runs of 3+ newlines to a single paragraph break.
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# Trailing whitespace per line (cosmetic, but keeps output tidy).
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)

# A line consisting entirely of bare formatting characters, possibly
# separated by whitespace.  Matches inputs like ``"###"``, ``"---"``,
# ``"# # #"``, and ``"--- ***"`` that slip past the heading/HR
# patterns because no trailing space follows them or because they
# mix marker families on one line.  Without this scrub the fallback
# path would hand those strings straight to the synthesizer, which
# would dutifully read "hash hash hash" or "dash dash dash" literally.
# We prefer silence (empty text raises ``ValueError`` downstream,
# which every canonical TTS caller already handles fail-soft) over
# the synthesizer voicing bare punctuation.
_BARE_FORMAT_LINE_RE = re.compile(
    r"^[ \t]*(?:[#\-*_>`~]+[ \t]*)+$",
    re.MULTILINE,
)


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


def _strip_bare_format_lines(text: str) -> str:
    """Remove lines whose entire content is bare formatting chars."""
    return _BARE_FORMAT_LINE_RE.sub("", text)


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
    * Inputs that are *entirely* formatting characters (e.g.
      ``"###"``, ``"---"``, ``"**"``) return an empty string.  The
      canonical TTS path turns empty text into a ``ValueError`` at
      ``build_tts_request`` which every caller already catches fail-
      soft, so the reply stays text-authoritative and the
      synthesizer is never asked to speak bare punctuation.

    The returned string is **only** suitable for TTS input -- callers
    must not substitute it for canonical assistant text displayed in
    the UI, stored in the session, or persisted to history.
    """
    if not text:
        return ""

    working = str(text)

    working = _strip_block_markers(working)
    working = _strip_inline_code(working)
    working = _strip_emphasis(working)
    # Second pass: inputs like ``"###"`` (no content after the
    # hashes) slip past ``_HEADING_RE`` which requires a trailing
    # space.  Strip whole lines that are bare formatting runs now so
    # the fallback below chooses silence over literal punctuation.
    working = _strip_bare_format_lines(working)
    working = _tidy_whitespace(working)

    # ``working`` is empty when the input contained nothing but
    # formatting characters.  Return empty so the downstream request
    # validator refuses the synthesis cleanly; speaking raw
    # punctuation would be louder and less truthful than silence.
    return working
