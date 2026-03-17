"""Bounded code/script/config draft intent classifier for Vera.

Detects requests to create code files, scripts, or configuration files and
produces authoritative preview-backed write_file artifacts.

Design rules:
- Detect a bounded set of language/file-type requests deterministically.
- Return a preview-ready write_file payload with an empty content placeholder.
- Actual code content is extracted from the LLM conversational reply and
  injected into the preview by the caller (vera_web/app.py) after reply
  generation.
- extract_code_from_reply() pulls the first fenced code block from the LLM
  reply so the preview gets real, authoritative content.
- Fail conservatively: return None when language/filename cannot be resolved.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Language / file-type registry (bounded set)
# ---------------------------------------------------------------------------

# Maps canonical lowercase key → {extension, fence, default_name}
_LANGUAGE_REGISTRY: dict[str, dict[str, str]] = {
    "python": {"extension": ".py", "fence": "python", "default_name": "script.py"},
    "bash": {"extension": ".sh", "fence": "bash", "default_name": "script.sh"},
    "shell": {"extension": ".sh", "fence": "bash", "default_name": "script.sh"},
    "sh": {"extension": ".sh", "fence": "bash", "default_name": "script.sh"},
    "yaml": {"extension": ".yaml", "fence": "yaml", "default_name": "config.yaml"},
    "yml": {"extension": ".yaml", "fence": "yaml", "default_name": "config.yaml"},
    "json": {"extension": ".json", "fence": "json", "default_name": "config.json"},
    "markdown": {"extension": ".md", "fence": "markdown", "default_name": "document.md"},
    "md": {"extension": ".md", "fence": "markdown", "default_name": "document.md"},
    "javascript": {"extension": ".js", "fence": "javascript", "default_name": "script.js"},
    "js": {"extension": ".js", "fence": "javascript", "default_name": "script.js"},
    "typescript": {"extension": ".ts", "fence": "typescript", "default_name": "script.ts"},
    "ts": {"extension": ".ts", "fence": "typescript", "default_name": "script.ts"},
    "ruby": {"extension": ".rb", "fence": "ruby", "default_name": "script.rb"},
    "rb": {"extension": ".rb", "fence": "ruby", "default_name": "script.rb"},
    "go": {"extension": ".go", "fence": "go", "default_name": "main.go"},
    "golang": {"extension": ".go", "fence": "go", "default_name": "main.go"},
    "rust": {"extension": ".rs", "fence": "rust", "default_name": "main.rs"},
    "rs": {"extension": ".rs", "fence": "rust", "default_name": "main.rs"},
    "sql": {"extension": ".sql", "fence": "sql", "default_name": "query.sql"},
    "html": {"extension": ".html", "fence": "html", "default_name": "index.html"},
    "css": {"extension": ".css", "fence": "css", "default_name": "style.css"},
    "toml": {"extension": ".toml", "fence": "toml", "default_name": "config.toml"},
    "ini": {"extension": ".ini", "fence": "ini", "default_name": "config.ini"},
    "xml": {"extension": ".xml", "fence": "xml", "default_name": "config.xml"},
    "powershell": {"extension": ".ps1", "fence": "powershell", "default_name": "script.ps1"},
    "ps1": {"extension": ".ps1", "fence": "powershell", "default_name": "script.ps1"},
    "kotlin": {"extension": ".kt", "fence": "kotlin", "default_name": "Main.kt"},
    "kt": {"extension": ".kt", "fence": "kotlin", "default_name": "Main.kt"},
    "swift": {"extension": ".swift", "fence": "swift", "default_name": "main.swift"},
    "java": {"extension": ".java", "fence": "java", "default_name": "Main.java"},
    "c": {"extension": ".c", "fence": "c", "default_name": "main.c"},
    "cpp": {"extension": ".cpp", "fence": "cpp", "default_name": "main.cpp"},
    "r": {"extension": ".r", "fence": "r", "default_name": "analysis.r"},
    "scala": {"extension": ".scala", "fence": "scala", "default_name": "main.scala"},
    "dockerfile": {"extension": "", "fence": "dockerfile", "default_name": "Dockerfile"},
}

# Extension → canonical language key (for filename-based detection)
_EXTENSION_TO_LANG: dict[str, str] = {}
for _lang, _cfg in _LANGUAGE_REGISTRY.items():
    _ext = _cfg["extension"].lstrip(".")
    if _ext and _lang not in _EXTENSION_TO_LANG.values():
        _EXTENSION_TO_LANG.setdefault(_ext, _lang)

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Verbs that signal intent to create something
_DRAFT_VERB_RE = re.compile(
    r"\b(?:write|create|make|build|draft|code|generate|give\s+me|write\s+me|make\s+me"
    r"|I\s+need(?:\s+you\s+to)?(?:\s+to)?)\b",
    re.IGNORECASE,
)

# Code-oriented subject nouns
_CODE_SUBJECT_RE = re.compile(
    r"\b(?:script|program|code|config(?:uration)?|template|snippet|example|file|document|dockerfile)\b",
    re.IGNORECASE,
)

# Language keywords — longer/unambiguous forms first to avoid masking.
# Single-letter tokens like "c" and "r" are intentionally excluded because
# they create too many false positives against common words (e.g. "Line C",
# "your answer"). Users requesting C or R scripts should use a filename
# (main.c, analysis.r) which is caught by _CODE_FILENAME_RE instead.
# "go" is included but requires a subject noun (script/program/config/etc.)
# which avoids false positives like "go ahead" or "I need to go" — those
# phrases lack a code-subject noun and will correctly return False.
# "md" alone is too ambiguous (part of many filenames / words); markdown doc
# requests should use the word "markdown" or an explicit .md filename.
_LANGUAGE_RE = re.compile(
    r"\b(python|bash|powershell|javascript|typescript|markdown|dockerfile|"
    r"golang|kotlin|swift|scala|ruby|rust|yaml|toml|html|json|xml|"
    r"css|sql|ini|shell|js|ts|rb|rs|ps1|kt|java|cpp|c\+\+|sh|go)\b",
    re.IGNORECASE,
)

# Filenames with known code extensions embedded in the message.
# The .md extension is included here so "write me a file called readme.md"
# correctly detects a markdown draft without needing "markdown" as a keyword.
_CODE_FILENAME_RE = re.compile(
    r"\b([a-zA-Z0-9_-]+\.(py|sh|yaml|yml|json|md|js|ts|rb|go|rs|sql|html|"
    r"css|toml|ini|xml|ps1|kt|swift|c|cpp|java|r|scala))\b",
    re.IGNORECASE,
)

# Exclusion: requests that reference existing/previous content are save-by-reference
# operations, NOT code draft requests.  Examples: "write that to a file",
# "save your previous answer to a note", "put that into answers.md".
_NOT_CODE_DRAFT_RE = re.compile(
    r"\b(?:"
    r"save\s+(?:that|this|the)\b"
    r"|write\s+(?:that|this)\b"
    r"|put\s+(?:that|this)\b"
    r"|write\s+(?:your|my|the|this|that)\s+(?:previous\s+)?(?:answer|response|summary|output|explanation)\b"
    r"|put\s+(?:your|my|the|this|that)\s+(?:previous\s+)?(?:answer|response|summary|output|explanation)\b"
    r"|save\s+(?:your|my|the|this|that)\s+(?:previous\s+)?(?:answer|response|summary|output|explanation)\b"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_code_draft_request(message: str) -> bool:
    """Return True if the message clearly requests a code/script/config file draft.

    Requires at least:
    - A creation/draft verb
    - A language keyword or filename with a code extension

    Excludes save-by-reference requests like "save that to a file".
    """
    text = message.strip()
    if not text:
        return False
    if _NOT_CODE_DRAFT_RE.search(text):
        return False
    has_verb = bool(_DRAFT_VERB_RE.search(text))
    if not has_verb:
        return False
    has_explicit_language = bool(_LANGUAGE_RE.search(text))
    has_code_filename = bool(_CODE_FILENAME_RE.search(text))
    if not has_explicit_language and not has_code_filename:
        return False
    # When an explicit filename with a code extension is present (e.g. "scraper.py"),
    # the intent is clear enough on its own — no extra subject noun required.
    if has_code_filename:
        return True
    # For language-keyword detection we additionally require a code-subject noun
    # (script, program, code, config, template, snippet, example, file) to avoid
    # false positives like "go ahead" or "make me a json response" where the user
    # wants in-chat content rather than a file draft.
    has_subject = bool(_CODE_SUBJECT_RE.search(text))
    return has_subject and has_explicit_language


def classify_code_draft_intent(message: str) -> dict[str, Any] | None:
    """Classify a message as a code/script/config draft request.

    Returns a preview-ready write_file payload if the intent is clearly a code
    draft, or None when language/filename cannot be resolved safely.

    The returned payload has an empty ``write_file.content`` placeholder.  The
    caller must inject actual code content (extracted from the LLM reply via
    ``extract_code_from_reply``) before persisting the preview.
    """
    if not is_code_draft_request(message):
        return None

    lang_key = _extract_language_key(message)
    if lang_key is None:
        return None

    config = _LANGUAGE_REGISTRY.get(lang_key)
    if config is None:
        return None

    filename = _extract_filename(message, config)
    path = f"~/VoxeraOS/notes/{filename}"

    kind = _draft_kind_label(config["extension"])
    goal = f"draft a {lang_key} {kind} as {filename}"

    return {
        "goal": goal,
        "write_file": {
            "path": path,
            "content": "",
            "mode": "overwrite",
        },
    }


def extract_code_from_reply(text: str) -> str | None:
    """Extract the first substantial fenced code block from an LLM reply.

    Matches ``` fenced blocks with or without a language specifier.
    Returns the code content (stripped) or None when no block is found.
    """
    if not text:
        return None
    # Match fenced code blocks: ```lang\ncode\n``` or ```\ncode\n```
    matches = re.findall(r"```(?:[a-zA-Z0-9_+\-.]*)?\n(.*?)```", text, re.DOTALL)
    for match in matches:
        content = match.strip()
        if content:
            return content
    return None


def has_code_file_extension(path: str) -> bool:
    """Return True if path ends with a known code/script/config file extension.

    Used to detect whether an existing write_file preview is a code-type file,
    which enables refinement detection (updating code content on follow-up turns).
    """
    if not path:
        return False
    # Collect unique extensions from the language registry
    _code_exts = {cfg["extension"] for cfg in _LANGUAGE_REGISTRY.values() if cfg["extension"]}
    for ext in _code_exts:
        if path.endswith(ext):
            return True
    # Dockerfile has no extension
    stripped = path.rstrip("/")
    return stripped.endswith("/Dockerfile") or stripped == "Dockerfile"


def code_fence_language(message: str) -> str | None:
    """Return the fence language tag for the language detected in message.

    Returns e.g. "python", "bash", "yaml" — or None if not detected.
    Useful for rendering code in fenced blocks in the UI.
    """
    lang_key = _extract_language_key(message)
    if lang_key is None:
        return None
    config = _LANGUAGE_REGISTRY.get(lang_key)
    if config is None:
        return None
    return config["fence"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_lang_token(token: str) -> str:
    """Normalize raw language match token to a registry key."""
    t = token.strip().lower()
    overrides = {
        "golang": "go",
        "c++": "cpp",
        "shell": "bash",
    }
    return overrides.get(t, t)


def _extract_language_key(message: str) -> str | None:
    """Extract the canonical language key from the message."""
    # Prefer explicit language keyword
    match = _LANGUAGE_RE.search(message)
    if match:
        return _normalize_lang_token(match.group(1))
    # Fall back to filename extension
    fname_match = _CODE_FILENAME_RE.search(message)
    if fname_match:
        ext = fname_match.group(2).lower()
        return _EXTENSION_TO_LANG.get(ext)
    return None


def _extract_filename(message: str, config: dict[str, str]) -> str:
    """Infer the output filename from the message or config defaults."""
    # Explicit filename in the message (e.g. "called scraper.py")
    fname_match = _CODE_FILENAME_RE.search(message)
    if fname_match:
        return fname_match.group(1)

    # "called X" or "named X" patterns
    named = re.search(r"\b(?:called|named)\s+([^\s]+)", message, re.IGNORECASE)
    if named:
        raw = named.group(1).strip("\"'.,;:!? ")
        if raw:
            ext = config["extension"]
            if ext and "." not in raw:
                raw = f"{raw}{ext}"
            return raw

    return config["default_name"]


def _draft_kind_label(extension: str) -> str:
    """Return a human-readable kind label for a given extension."""
    script_exts = {
        ".py",
        ".sh",
        ".js",
        ".ts",
        ".rb",
        ".go",
        ".rs",
        ".ps1",
        ".kt",
        ".swift",
        ".c",
        ".cpp",
        ".java",
        ".r",
        ".scala",
        ".sql",
    }
    config_exts = {".yaml", ".yml", ".json", ".toml", ".ini", ".xml"}
    doc_exts = {".md", ".html", ".css"}
    if extension in script_exts:
        return "script"
    if extension in config_exts:
        return "config file"
    if extension in doc_exts:
        return "document"
    return "file"
