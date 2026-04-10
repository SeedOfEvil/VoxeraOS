"""Safe bounded markdown renderer for Vera assistant messages.

Converts a limited markdown subset into safe HTML.  The input is
HTML-escaped **first**, then markdown patterns are transformed into a
fixed set of safe HTML elements.  This makes it impossible for
assistant content to inject arbitrary HTML or scripts.

Supported subset
~~~~~~~~~~~~~~~~
* Headings:  ``#`` / ``##`` / ``###``
* Bold:  ``**text**``
* Inline code:  ```text```
* Fenced code blocks:  ```````` ... ````````
* Unordered lists:  ``- item`` or ``* item``
* Ordered lists:  ``1. item``
* Blockquotes:  ``> text``
* Paragraph breaks
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------

_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _render_inline(text: str) -> str:
    """Apply inline markdown formatting to already-escaped text.

    Inline code spans are extracted first so their contents are protected
    from bold processing.
    """
    # 1. Extract code spans into placeholders
    code_spans: list[str] = []

    def _save_code(m: re.Match[str]) -> str:
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    text = _CODE_SPAN_RE.sub(_save_code, text)

    # 2. Bold
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)

    # 3. Restore code spans
    for idx, content in enumerate(code_spans):
        text = text.replace(f"\x00CODE{idx}\x00", f"<code>{content}</code>")

    return text


# ---------------------------------------------------------------------------
# Block-level helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_UL_RE = re.compile(r"^[-*]\s+(.+)$")
_OL_RE = re.compile(r"^\d+\.\s+(.+)$")


def _collect_list_items(
    lines: list[str],
    start: int,
    item_re: re.Pattern[str],
) -> tuple[list[str], int]:
    """Collect consecutive list items, tolerating blank lines between them."""
    items: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        m = item_re.match(s)
        if m:
            items.append(f"<li>{_render_inline(m.group(1))}</li>")
            i += 1
        elif s == "":
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and item_re.match(lines[j].strip()):
                i += 1
            else:
                break
        else:
            break
    return items, i


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_assistant_markdown(text: str) -> Markup:
    """Render a bounded markdown subset from *text* into safe HTML.

    Returns a :class:`~markupsafe.Markup` instance so Jinja2 autoescape
    will **not** double-escape the result.
    """
    if not text:
        return Markup("")

    # ── Step 1: HTML-escape the entire input ─────────────────────────
    escaped = str(escape(text))

    lines = escaped.split("\n")
    parts: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        stripped = lines[i].strip()

        # ── Fenced code block ────────────────────────────────────────
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < n:
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            code_body = "\n".join(code_lines)
            parts.append(f"<pre><code>{code_body}</code></pre>")
            continue

        # ── Heading (#, ##, ###) ─────────────────────────────────────
        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            tag = f"h{level + 2}"  # # → h3, ## → h4, ### → h5
            parts.append(f"<{tag}>{_render_inline(m.group(2))}</{tag}>")
            i += 1
            continue

        # ── Unordered list (- or *) ──────────────────────────────────
        if _UL_RE.match(stripped):
            items, i = _collect_list_items(lines, i, _UL_RE)
            parts.append(f"<ul>{''.join(items)}</ul>")
            continue

        # ── Ordered list (1. 2. …) ──────────────────────────────────
        if _OL_RE.match(stripped):
            items, i = _collect_list_items(lines, i, _OL_RE)
            parts.append(f"<ol>{''.join(items)}</ol>")
            continue

        # ── Blockquote (> …)  — escaped as &gt; ─────────────────────
        if stripped.startswith("&gt; ") or stripped == "&gt;":
            q_lines: list[str] = []
            while i < n:
                s = lines[i].strip()
                if s.startswith("&gt; "):
                    q_lines.append(_render_inline(s[5:]))
                    i += 1
                elif s == "&gt;":
                    q_lines.append("")
                    i += 1
                else:
                    break
            parts.append(f"<blockquote>{'<br>'.join(q_lines)}</blockquote>")
            continue

        # ── Blank line ───────────────────────────────────────────────
        if stripped == "":
            i += 1
            continue

        # ── Paragraph ────────────────────────────────────────────────
        p_lines: list[str] = []
        while i < n:
            s = lines[i].strip()
            if (
                s == ""
                or _HEADING_RE.match(s)
                or _UL_RE.match(s)
                or _OL_RE.match(s)
                or s.startswith("```")
                or s.startswith("&gt; ")
                or s == "&gt;"
            ):
                break
            p_lines.append(_render_inline(s))
            i += 1
        parts.append(f"<p>{'<br>'.join(p_lines)}</p>")

    return Markup("\n".join(parts))
