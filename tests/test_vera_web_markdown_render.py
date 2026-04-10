"""Tests for the safe bounded markdown renderer used in Vera assistant messages."""

from __future__ import annotations

from markupsafe import Markup

from voxera.vera_web.markdown_render import render_assistant_markdown

# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


class TestPlainText:
    def test_plain_text_wrapped_in_paragraph(self):
        result = render_assistant_markdown("Hello world")
        assert "<p>Hello world</p>" in result

    def test_empty_string_returns_empty_markup(self):
        result = render_assistant_markdown("")
        assert result == Markup("")

    def test_none_returns_empty_markup(self):
        result = render_assistant_markdown(None)  # type: ignore[arg-type]
        assert result == Markup("")

    def test_multiline_plain_text_joins_with_br(self):
        result = render_assistant_markdown("line one\nline two")
        assert "<p>line one<br>line two</p>" in result

    def test_return_type_is_markup(self):
        result = render_assistant_markdown("hello")
        assert isinstance(result, Markup)


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


class TestHeadings:
    def test_h1_renders_as_h3(self):
        result = render_assistant_markdown("# Title")
        assert "<h3>Title</h3>" in result

    def test_h2_renders_as_h4(self):
        result = render_assistant_markdown("## Section")
        assert "<h4>Section</h4>" in result

    def test_h3_renders_as_h5(self):
        result = render_assistant_markdown("### Subsection")
        assert "<h5>Subsection</h5>" in result

    def test_heading_not_rendered_without_space(self):
        result = render_assistant_markdown("#nospace")
        assert "<h3>" not in result
        assert "<p>#nospace</p>" in result

    def test_heading_with_bold(self):
        result = render_assistant_markdown("## **Bold Heading**")
        assert "<h4><strong>Bold Heading</strong></h4>" in result

    def test_heading_with_inline_code(self):
        result = render_assistant_markdown("## The `main` function")
        assert "<h4>The <code>main</code> function</h4>" in result

    def test_heading_raw_markers_not_visible(self):
        """### Heading should not appear as literal '### Heading'."""
        result = render_assistant_markdown("### What I Do")
        assert "###" not in result
        assert "<h5>What I Do</h5>" in result


# ---------------------------------------------------------------------------
# Bold
# ---------------------------------------------------------------------------


class TestBold:
    def test_bold_renders_as_strong(self):
        result = render_assistant_markdown("This is **bold** text")
        assert "<strong>bold</strong>" in result
        assert "**" not in result

    def test_multiple_bold_in_same_line(self):
        result = render_assistant_markdown("**one** and **two**")
        assert "<strong>one</strong>" in result
        assert "<strong>two</strong>" in result

    def test_bold_raw_markers_not_visible(self):
        """**bold** should not appear as literal asterisks."""
        result = render_assistant_markdown("**important**")
        assert "**" not in result
        assert "<strong>important</strong>" in result


# ---------------------------------------------------------------------------
# Inline code
# ---------------------------------------------------------------------------


class TestInlineCode:
    def test_inline_code_renders(self):
        result = render_assistant_markdown("Use the `Preview` command")
        assert "<code>Preview</code>" in result
        assert "`" not in result

    def test_bold_inside_code_not_rendered(self):
        """Content inside backticks should not be processed for bold."""
        result = render_assistant_markdown("`**not bold**`")
        assert "<strong>" not in result
        assert "<code>**not bold**</code>" in result

    def test_inline_code_renders_correctly(self):
        result = render_assistant_markdown("Run `npm install` first")
        assert "<code>npm install</code>" in result


# ---------------------------------------------------------------------------
# Unordered lists
# ---------------------------------------------------------------------------


class TestUnorderedLists:
    def test_dash_list(self):
        text = "- first\n- second\n- third"
        result = render_assistant_markdown(text)
        assert "<ul>" in result
        assert "<li>first</li>" in result
        assert "<li>second</li>" in result
        assert "<li>third</li>" in result

    def test_asterisk_list(self):
        text = "* alpha\n* beta"
        result = render_assistant_markdown(text)
        assert "<ul>" in result
        assert "<li>alpha</li>" in result

    def test_list_with_bold_items(self):
        text = "- **bold item**\n- plain item"
        result = render_assistant_markdown(text)
        assert "<li><strong>bold item</strong></li>" in result
        assert "<li>plain item</li>" in result

    def test_bullet_list_renders_as_list(self):
        """Bullet markers should not appear as raw text."""
        text = "* I don't execute work directly.\n* I don't guess at results."
        result = render_assistant_markdown(text)
        assert "<ul>" in result
        assert "* " not in result


# ---------------------------------------------------------------------------
# Ordered lists
# ---------------------------------------------------------------------------


class TestOrderedLists:
    def test_ordered_list(self):
        text = "1. first\n2. second\n3. third"
        result = render_assistant_markdown(text)
        assert "<ol>" in result
        assert "<li>first</li>" in result
        assert "<li>third</li>" in result

    def test_ordered_list_with_inline_formatting(self):
        text = "1. **Reasoning:** I help refine goals.\n2. **Drafting:** I prepare a `Preview`."
        result = render_assistant_markdown(text)
        assert "<ol>" in result
        assert "<strong>Reasoning:</strong>" in result
        assert "<code>Preview</code>" in result

    def test_numbered_list_renders_as_list(self):
        """Numbered list markers should not appear as raw text."""
        text = "1. Step one\n2. Step two"
        result = render_assistant_markdown(text)
        assert "<ol>" in result
        assert "1." not in result


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------


class TestFencedCodeBlocks:
    def test_fenced_code_block(self):
        text = "```\nprint('hello')\n```"
        result = render_assistant_markdown(text)
        assert "<pre><code>" in result
        assert "print(" in result
        assert "</code></pre>" in result

    def test_fenced_block_with_language(self):
        text = "```python\nx = 1\n```"
        result = render_assistant_markdown(text)
        assert "<pre><code>" in result
        assert "x = 1" in result

    def test_fenced_block_content_not_inline_processed(self):
        """Bold markers inside code blocks should stay literal."""
        text = "```\n**not bold**\n```"
        result = render_assistant_markdown(text)
        assert "<strong>" not in result
        assert "**not bold**" in result

    def test_unclosed_fenced_block_consumes_remaining(self):
        text = "```\ncode without close"
        result = render_assistant_markdown(text)
        assert "<pre><code>" in result
        assert "code without close" in result


# ---------------------------------------------------------------------------
# Blockquotes
# ---------------------------------------------------------------------------


class TestBlockquotes:
    def test_blockquote(self):
        text = "> This is quoted"
        result = render_assistant_markdown(text)
        assert "<blockquote>" in result
        assert "This is quoted" in result

    def test_multiline_blockquote(self):
        text = "> line one\n> line two"
        result = render_assistant_markdown(text)
        assert "<blockquote>" in result
        assert "line one" in result
        assert "line two" in result


# ---------------------------------------------------------------------------
# Safety / escaping
# ---------------------------------------------------------------------------


class TestSafety:
    def test_html_tags_escaped(self):
        result = render_assistant_markdown("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_html_attributes_escaped(self):
        result = render_assistant_markdown('<img onerror="alert(1)">')
        # markupsafe encodes " as &#34; — either form is safe
        assert "onerror" not in result or "&#34;" in result or "&quot;" in result
        assert "<img" not in result

    def test_heading_with_html_injection(self):
        result = render_assistant_markdown("# <script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_bold_with_html_injection(self):
        result = render_assistant_markdown("**<b>nested</b>**")
        assert "<b>" not in result
        assert "&lt;b&gt;" in result

    def test_inline_code_with_html(self):
        result = render_assistant_markdown("`<div>tag</div>`")
        assert "<div>" not in result
        assert "&lt;div&gt;" in result

    def test_list_item_with_html(self):
        result = render_assistant_markdown("- <a href='evil'>click</a>")
        assert "<a " not in result
        assert "&lt;a " in result

    def test_ampersand_escaped(self):
        result = render_assistant_markdown("AT&T")
        assert "AT&amp;T" in result


# ---------------------------------------------------------------------------
# User messages unchanged
# ---------------------------------------------------------------------------


class TestUserMessagesUnchanged:
    """The renderer is only applied to assistant messages via the template/JS.

    This test verifies the renderer itself does not distinguish roles —
    the role filtering happens at the call site (template and JS).
    The renderer always transforms its input, so user-message safety
    is enforced by *not calling* the renderer for user messages.
    """

    def test_renderer_always_transforms(self):
        """Renderer transforms any input — caller is responsible for scoping."""
        result = render_assistant_markdown("**bold**")
        assert "<strong>bold</strong>" in result


# ---------------------------------------------------------------------------
# Combined / realistic sample
# ---------------------------------------------------------------------------


class TestRealisticSample:
    """Test the concrete sample from the PR spec."""

    SAMPLE = (
        "### What I Do\n"
        "\n"
        "1. **Reasoning and Clarification:** I help refine goals.\n"
        "2. **Drafting Work:** I prepare a `Preview`.\n"
        "\n"
        "### What I Don't Do\n"
        "\n"
        "* I don't execute work directly.\n"
        "* I don't guess at results."
    )

    def test_headings_rendered(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "<h5>What I Do</h5>" in result
        assert "What I Don&#39;t Do" in result or "What I Don&" in result
        assert "###" not in result

    def test_bold_rendered(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "<strong>Reasoning and Clarification:</strong>" in result
        assert "<strong>Drafting Work:</strong>" in result
        assert "**" not in result

    def test_numbered_list_rendered(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "<ol>" in result
        assert "<li>" in result

    def test_bullet_list_rendered(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "<ul>" in result

    def test_inline_code_rendered(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "<code>Preview</code>" in result

    def test_no_raw_markers_visible(self):
        result = render_assistant_markdown(self.SAMPLE)
        assert "###" not in result
        assert "**" not in result
        assert "* I don" not in result  # bullet should be list, not raw

    def test_plain_text_still_renders(self):
        result = render_assistant_markdown("Just some normal text.")
        assert "<p>Just some normal text.</p>" in result


# ---------------------------------------------------------------------------
# Paragraph breaks
# ---------------------------------------------------------------------------


class TestParagraphBreaks:
    def test_blank_line_creates_separate_paragraphs(self):
        result = render_assistant_markdown("para one\n\npara two")
        assert "<p>para one</p>" in result
        assert "<p>para two</p>" in result

    def test_heading_then_paragraph(self):
        result = render_assistant_markdown("# Title\n\nSome text.")
        assert "<h3>Title</h3>" in result
        assert "<p>Some text.</p>" in result
