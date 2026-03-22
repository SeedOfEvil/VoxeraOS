"""Bounded file intent classifier for Vera/planner routing.

Detects natural-language user requests that map to bounded file skills
(files.exists, files.stat, files.read_text, files.mkdir, files.delete_file,
files.copy_file, files.move_file) or the structured file_organize queue contract.

Returns preview-ready payloads or None when intent is unclear.
All paths are confined to ~/VoxeraOS/notes/ scope.
"""

from __future__ import annotations

import re
from typing import Any

_NOTES_ROOT = "~/VoxeraOS/notes"
_QUEUE_SEGMENT = "queue"

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_EXPLICIT_PATH_RE = re.compile(r"(~/[^\s]+|/home/[^\s]+)")

# Workspace-root-relative shorthand: /foo/bar.txt or /foo — a leading slash
# followed by a non-space path component.  Must contain at least one path char
# after the slash to avoid matching stray punctuation.
_WORKSPACE_RELATIVE_PATH_RE = re.compile(r"(/[a-zA-Z0-9_][^\s]*)")

# A filename looks like: word.ext or word/subpath — must contain a dot or slash
# to distinguish from natural-language words.
_FILENAME_WITH_EXT_RE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.[a-zA-Z0-9]{1,8}")
# A bare directory name (no dot): only valid when context makes it clear
_BARE_NAME_RE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_-]*")


def _contains_parent_traversal(path: str) -> bool:
    return ".." in path.replace("\\", "/").split("/")


def is_safe_notes_path(path: str) -> bool:
    """Return True if path is safely within ~/VoxeraOS/notes/ and not in queue."""
    if _contains_parent_traversal(path):
        return False
    normalized = path.rstrip("/")
    if not normalized.startswith(_NOTES_ROOT):
        return False
    after_root = normalized[len(_NOTES_ROOT) :].lstrip("/")
    return after_root != _QUEUE_SEGMENT and not after_root.startswith(f"{_QUEUE_SEGMENT}/")


def _normalize_notes_path(name: str) -> str:
    """Normalize a bare filename or relative name to a full notes path.

    Supported forms:
    - ``~/VoxeraOS/notes/foo.txt``  → returned as-is (explicit)
    - ``/home/.../foo.txt``         → returned as-is (explicit)
    - ``/foo/bar.txt``              → workspace-root-relative shorthand
                                      → ``~/VoxeraOS/notes/foo/bar.txt``
    - ``foo.txt``                   → bare name → ``~/VoxeraOS/notes/foo.txt``
    """
    name = name.strip().strip("\"'`.,;:!? ")
    if not name:
        return ""
    if name.startswith("~/") or name.startswith("/home/"):
        return name
    # Workspace-root-relative shorthand: /foo → ~/VoxeraOS/notes/foo
    if name.startswith("/"):
        return f"{_NOTES_ROOT}/{name.lstrip('/')}"
    return f"{_NOTES_ROOT}/{name}"


def _extract_path_from_text(text: str, *, allow_bare_name: bool = False) -> str | None:
    """Extract the best file/directory path token from text.

    Prefers explicit paths (~/...) then filenames with extensions (foo.txt),
    and only falls back to bare names if allow_bare_name is True.
    """
    # Try explicit path first
    explicit = _EXPLICIT_PATH_RE.search(text)
    if explicit:
        return explicit.group(1).rstrip(".,;:!?\"' ")

    # Try workspace-root-relative shorthand (/foo/bar.txt)
    ws_rel = _WORKSPACE_RELATIVE_PATH_RE.search(text)
    if ws_rel:
        token = ws_rel.group(1).rstrip(".,;:!?\"' ")
        # Reject if the surrounding text contains parent traversal that the
        # regex skipped over (e.g. "../..//etc/passwd" → extracted "/etc/passwd").
        if not _contains_parent_traversal(text):
            return token

    # Try filename with extension
    ext_match = _FILENAME_WITH_EXT_RE.search(text)
    if ext_match:
        return ext_match.group(0).rstrip(".,;:!?\"' ")

    if allow_bare_name:
        # Try bare name: skip common stop words (but not directory names like "archive")
        stop_words = {
            "a",
            "an",
            "the",
            "this",
            "that",
            "it",
            "my",
            "in",
            "to",
            "into",
            "from",
            "for",
            "of",
            "is",
            "if",
            "me",
            "called",
            "named",
            "exists",
            "exist",
            "there",
            "folder",
            "directory",
            "dir",
            "notes",
            "note",
            "file",
            "about",
            "info",
            "information",
            "details",
            "metadata",
            "on",
            "and",
            "or",
            "please",
            "check",
            "whether",
            "does",
            "show",
            "see",
            "verify",
            "copy",
            "move",
            "rename",
            "delete",
            "remove",
            "make",
            "create",
            "add",
            "inspect",
            "examine",
            "stats",
            "stat",
            "read",
            "cat",
            "display",
            "print",
            "output",
        }
        for word in text.split():
            cleaned = word.strip("\"'`.,;:!? ")
            if not cleaned or cleaned.lower() in stop_words:
                continue
            if _BARE_NAME_RE.fullmatch(cleaned):
                return cleaned
    return None


# ---------------------------------------------------------------------------
# Intent patterns
# ---------------------------------------------------------------------------

_RE_EXISTS = re.compile(
    r"\b(?:check\s+(?:if|whether)|does|is)\b.*\b(?:exist|exists|there)\b"
    r"|\b(?:see\s+if|verify\s+(?:if|that|whether))\b.*\b(?:exist|exists|is\s+there)\b",
    re.IGNORECASE,
)

_RE_STAT = re.compile(
    r"\b(?:show\s+(?:me\s+)?)?(?:info(?:rmation)?|details?|metadata|stats?)\s+"
    r"(?:about|for|on|of)\b"
    r"|\b(?:file\s+info|what(?:'s|\s+is)\s+(?:the\s+)?(?:info|details?|metadata|size|stats?)\s+(?:of|for|about|on))\b"
    r"|\b(?:inspect|examine)\s+(?:the\s+)?(?:file|metadata)\b",
    re.IGNORECASE,
)

_RE_READ = re.compile(
    r"\b(?:read|cat|display|print|output)\s+(?:the\s+)?(?:file\s+)?",
    re.IGNORECASE,
)

_RE_MKDIR = re.compile(
    r"\b(?:make|create|add)\s+(?:a\s+)?(?:folder|directory|dir)\b"
    r"|\bmkdir\b",
    re.IGNORECASE,
)

_RE_DELETE = re.compile(
    r"\b(?:delete|remove|rm)\s+(?:the\s+)?(?:file\s+)?",
    re.IGNORECASE,
)

_RE_COPY = re.compile(
    r"\bcopy\s+",
    re.IGNORECASE,
)

_RE_MOVE = re.compile(
    r"\b(?:move|rename)\s+",
    re.IGNORECASE,
)

_RE_ARCHIVE_ORGANIZE = re.compile(
    r"\b(?:archive|organize|file\s+away|sort)\b.*\b(?:into|to|in)\b",
    re.IGNORECASE,
)

_RE_DESTINATION_SPLIT = re.compile(
    r"\s+(?:to|into|in(?:side)?)\s+(?:(?:my|the)\s+)?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Intent classifiers
# ---------------------------------------------------------------------------


def _classify_exists(text: str) -> dict[str, Any] | None:
    if not _RE_EXISTS.search(text):
        return None
    # Extract path: look for filenames in the region between verb and "exists/there"
    path_token = _extract_path_from_text(text)
    if not path_token:
        return None
    full_path = _normalize_notes_path(path_token)
    if not is_safe_notes_path(full_path):
        return None
    return {
        "goal": f"check if {path_token} exists in notes",
        "steps": [{"skill_id": "files.exists", "args": {"path": full_path}}],
    }


def _classify_stat(text: str) -> dict[str, Any] | None:
    if not _RE_STAT.search(text):
        return None
    path_token = _extract_path_from_text(text)
    if not path_token:
        return None
    full_path = _normalize_notes_path(path_token)
    if not is_safe_notes_path(full_path):
        return None
    return {
        "goal": f"show file info for {path_token}",
        "steps": [{"skill_id": "files.stat", "args": {"path": full_path}}],
    }


def _classify_read(text: str) -> dict[str, Any] | None:
    if not _RE_READ.search(text):
        return None
    # Don't match "read" inside archive/organize compound phrases
    if _RE_ARCHIVE_ORGANIZE.search(text):
        return None
    read_match = _RE_READ.search(text)
    if not read_match:
        return None
    after_verb = text[read_match.end() :].strip()
    path_token = _extract_path_from_text(after_verb) or _extract_path_from_text(text)
    if not path_token:
        return None
    full_path = _normalize_notes_path(path_token)
    if not is_safe_notes_path(full_path):
        return None
    return {
        "goal": f"read {path_token} from notes",
        "steps": [{"skill_id": "files.read_text", "args": {"path": full_path}}],
    }


def _classify_mkdir(text: str) -> dict[str, Any] | None:
    if not _RE_MKDIR.search(text):
        return None
    # Extract directory name: "make a folder called X" or "create folder X in my notes"
    called_match = re.search(r"\b(?:called|named)\s+([^\s]+)", text, re.IGNORECASE)
    if called_match:
        dir_name = called_match.group(1).strip("\"'`.,;:!? ")
    else:
        # Try to find directory name after "folder/directory/dir"
        dir_name_match = re.search(
            r"\b(?:folder|directory|dir)\s+([a-zA-Z0-9_][a-zA-Z0-9_./-]*)",
            text,
            re.IGNORECASE,
        )
        if dir_name_match:
            dir_name = dir_name_match.group(1).strip("\"'`.,;:!? ")
        else:
            return None
    if not dir_name or dir_name.lower() in {"in", "to", "my", "the", "called", "named"}:
        return None
    full_path = _normalize_notes_path(dir_name)
    if not is_safe_notes_path(full_path):
        return None
    return {
        "goal": f"create folder {dir_name} in notes",
        "steps": [
            {
                "skill_id": "files.mkdir",
                "args": {"path": full_path, "parents": True},
            }
        ],
    }


def _classify_delete(text: str) -> dict[str, Any] | None:
    if not _RE_DELETE.search(text):
        return None
    # Don't match "delete" in compound archive/organize phrases
    if _RE_ARCHIVE_ORGANIZE.search(text):
        return None
    # Extract path after the delete verb
    delete_match = re.search(
        r"\b(?:delete|remove|rm)\s+(?:the\s+)?(?:file\s+)?", text, re.IGNORECASE
    )
    if not delete_match:
        return None
    after_verb = text[delete_match.end() :].strip()
    path_token = _extract_path_from_text(after_verb)
    if not path_token:
        return None
    full_path = _normalize_notes_path(path_token)
    if not is_safe_notes_path(full_path):
        return None
    return {
        "goal": f"delete {path_token} from notes",
        "steps": [{"skill_id": "files.delete_file", "args": {"path": full_path}}],
    }


def _split_on_destination(text: str) -> tuple[str, str] | None:
    """Split text on destination preposition (to/into). Returns (before, after) or None."""
    match = _RE_DESTINATION_SPLIT.search(text)
    if not match:
        return None
    before = text[: match.start()].strip()
    after = text[match.end() :].strip()
    if before and after:
        return (before, after)
    return None


def _classify_copy_or_move(text: str) -> dict[str, Any] | None:
    is_copy = _RE_COPY.search(text)
    is_move = _RE_MOVE.search(text)
    if not is_copy and not is_move:
        return None
    # Archive/organize gets its own classifier
    if _RE_ARCHIVE_ORGANIZE.search(text):
        return None

    mode = "copy" if is_copy else "move"
    verb = "copy" if is_copy else "move"

    # Split on verb to get after-verb text, then split on destination preposition
    verb_match = re.search(rf"\b{verb}\s+", text, re.IGNORECASE)
    if not verb_match:
        return None
    after_verb = text[verb_match.end() :].strip()

    parts = _split_on_destination(after_verb)
    if not parts:
        return None
    source_text, dest_text = parts

    source_token = _extract_path_from_text(source_text)
    dest_token = _extract_path_from_text(dest_text, allow_bare_name=True)
    if not source_token or not dest_token:
        return None

    source_path = _normalize_notes_path(source_token)
    dest_path = _normalize_notes_path(dest_token)
    if not is_safe_notes_path(source_path) or not is_safe_notes_path(dest_path):
        return None

    # Determine if dest looks like a directory (no extension) or a file
    dest_has_extension = "." in dest_token.rsplit("/", 1)[-1]
    if dest_has_extension:
        # Direct file-to-file: use file_organize with dest_dir = parent
        dest_dir = str(dest_path).rsplit("/", 1)[0] if "/" in dest_path else _NOTES_ROOT
        return {
            "goal": f"{verb} {source_token} to {dest_token}",
            "file_organize": {
                "source_path": source_path,
                "destination_dir": dest_dir,
                "mode": mode,
                "overwrite": False,
                "delete_original": False,
            },
        }
    else:
        # Destination is a directory
        return {
            "goal": f"{verb} {source_token} into {dest_token}",
            "file_organize": {
                "source_path": source_path,
                "destination_dir": dest_path,
                "mode": mode,
                "overwrite": False,
                "delete_original": False,
            },
        }


def _classify_archive_organize(text: str) -> dict[str, Any] | None:
    if not _RE_ARCHIVE_ORGANIZE.search(text):
        return None

    # Extract source: the filename/path after the archive verb
    source_match = re.search(
        r"\b(?:archive|organize|file\s+away|sort)\s+(?:this\s+)?(?:note\s+)?(?:file\s+)?",
        text,
        re.IGNORECASE,
    )
    if not source_match:
        return None

    after_verb = text[source_match.end() :].strip()
    parts = _split_on_destination(after_verb)
    if parts:
        source_text, dest_text = parts
    else:
        # Try splitting the full after-verb text
        return None

    source_token = _extract_path_from_text(source_text)
    dest_token = _extract_path_from_text(dest_text, allow_bare_name=True)

    if not source_token or not dest_token:
        return None

    source_path = _normalize_notes_path(source_token)
    dest_path = _normalize_notes_path(dest_token)
    if not is_safe_notes_path(source_path) or not is_safe_notes_path(dest_path):
        return None

    return {
        "goal": f"archive {source_token} into {dest_token}",
        "file_organize": {
            "source_path": source_path,
            "destination_dir": dest_path,
            "mode": "copy",
            "overwrite": False,
            "delete_original": False,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_blocked_file_intent(message: str) -> str | None:
    """Detect when a file intent pattern matches but the path is blocked.

    Returns a human-readable refusal message when:
    - An intent pattern (exists/stat/read/mkdir/delete/copy/move/archive) matches
    - A path token can be extracted
    - The normalized path fails safety (queue control-plane, parent traversal, scope)

    Returns ``None`` when no intent pattern matches, or when the path is safe
    (i.e. ``classify_bounded_file_intent`` would handle it).
    """
    text = message.strip()
    if not text:
        return None

    # Check each intent pattern — if one matches and path is blocked, return refusal
    intent_patterns = [
        (_RE_EXISTS, "existence check"),
        (_RE_STAT, "file info"),
        (_RE_READ, "file read"),
        (_RE_MKDIR, "directory creation"),
        (_RE_DELETE, "file deletion"),
        (_RE_COPY, "file copy"),
        (_RE_MOVE, "file move"),
        (_RE_ARCHIVE_ORGANIZE, "file archive"),
    ]
    for pattern, label in intent_patterns:
        if not pattern.search(text):
            continue
        # Intent pattern matched — check for parent traversal in the raw text
        # even if path extraction fails (the traversal guard may suppress extraction).
        if _contains_parent_traversal(text):
            return (
                f"I can't perform a {label} on that path — "
                f"it's blocked by the bounded filesystem safety boundary (parent traversal (..)). "
                f"All file operations are confined to the ~/VoxeraOS/notes/ workspace, "
                f"excluding the queue control-plane directory."
            )
        # Check if a path can be extracted
        path_token = _extract_path_from_text(text)
        if not path_token:
            continue
        full_path = _normalize_notes_path(path_token)
        if is_safe_notes_path(full_path):
            # Path is safe — classify_bounded_file_intent handles this
            return None
        # Path is blocked — produce refusal
        if _QUEUE_SEGMENT in full_path.split("/"):
            reason = "queue/control-plane scope"
        else:
            reason = "outside bounded workspace scope"
        return (
            f"I can't perform a {label} on that path — "
            f"it's blocked by the bounded filesystem safety boundary ({reason}). "
            f"All file operations are confined to the ~/VoxeraOS/notes/ workspace, "
            f"excluding the queue control-plane directory."
        )

    return None


def classify_bounded_file_intent(message: str) -> dict[str, Any] | None:
    """Classify a user message into a bounded file intent if clear.

    Returns a preview-ready payload dict with either:
    - ``steps`` (for single-skill actions like exists/stat/mkdir/delete)
    - ``file_organize`` (for copy/move/archive/organize workflows)
    - ``None`` if no bounded file intent is detected or paths are ambiguous.

    All paths are confined to ~/VoxeraOS/notes/ scope and reject queue paths.
    """
    text = message.strip()
    if not text:
        return None

    # Try classifiers in specificity order: most specific first
    result = _classify_archive_organize(text)
    if result is not None:
        return result

    result = _classify_copy_or_move(text)
    if result is not None:
        return result

    result = _classify_exists(text)
    if result is not None:
        return result

    result = _classify_stat(text)
    if result is not None:
        return result

    result = _classify_read(text)
    if result is not None:
        return result

    result = _classify_mkdir(text)
    if result is not None:
        return result

    result = _classify_delete(text)
    if result is not None:
        return result

    return None
