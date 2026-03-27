from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"
_MAX_MATCHES_CAP = 2000
_MAX_FILE_BYTES_CAP = 5_000_000


def _resolve_existing_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def _walk_files(*, root: Path, include_hidden: bool, max_depth: int):
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if current.is_file():
            yield current
            continue
        if depth >= max_depth or not current.is_dir():
            continue
        children = sorted(current.iterdir(), key=lambda item: item.name.lower(), reverse=True)
        for child in children:
            if not include_hidden and child.name.startswith("."):
                continue
            stack.append((child, depth + 1))


def run(
    root_path: str,
    pattern: str,
    case_sensitive: bool = False,
    max_depth: int = 8,
    include_hidden: bool = False,
    max_matches: int = 200,
    max_file_bytes: int = 1_000_000,
) -> RunResult:
    try:
        root = _resolve_existing_safe_path(root_path)
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"Root path not found: {root_path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Root path not found: {root_path}",
                    machine_payload={"root_path": root_path, "pattern": pattern},
                    operator_note="Provide an existing root path under notes scope.",
                    next_action_hint="provide_existing_path",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"Root path not found: {root_path}",
                    error_class="not_found",
                )
            },
        )
    except PathBoundaryError as exc:
        return RunResult(
            ok=False,
            error=str(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected grep outside allowlist",
                    machine_payload={"root_path": root_path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Grep root must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=exc.error_class == "path_blocked_scope",
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    if not pattern.strip():
        return RunResult(
            ok=False,
            error="pattern must be a non-empty string",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected empty grep pattern",
                    machine_payload={"pattern": pattern},
                    operator_note="Provide a non-empty text pattern to search.",
                    next_action_hint="provide_pattern",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error="pattern must be a non-empty string",
                    error_class="invalid_input",
                )
            },
        )

    capped_depth = max(0, min(max_depth, 32))
    capped_matches = max(1, min(max_matches, _MAX_MATCHES_CAP))
    capped_file_bytes = max(1, min(max_file_bytes, _MAX_FILE_BYTES_CAP))

    needle = pattern if case_sensitive else pattern.lower()
    matches: list[dict[str, object]] = []
    files_scanned = 0
    files_skipped_large = 0
    files_skipped_binary = 0
    truncated = False

    try:
        for file_path in _walk_files(
            root=root, include_hidden=include_hidden, max_depth=capped_depth
        ):
            files_scanned += 1
            try:
                size_bytes = file_path.stat().st_size
            except OSError:
                continue
            if size_bytes > capped_file_bytes:
                files_skipped_large += 1
                continue

            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                files_skipped_binary += 1
                continue

            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle not in haystack:
                    continue
                relative = file_path.relative_to(ALLOWED_ROOT.expanduser().resolve(strict=False))
                matches.append(
                    {
                        "path": str(relative),
                        "line_number": line_number,
                        "line_excerpt": line[:240],
                    }
                )
                if len(matches) >= capped_matches:
                    truncated = True
                    break
            if truncated:
                break

        summary = f"Found {len(matches)} matches for '{pattern}' under {root}"
        if truncated:
            summary = f"Found {len(matches)} matches for '{pattern}' under {root} (truncated)"

        return RunResult(
            ok=True,
            output=summary,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=summary,
                    machine_payload={
                        "root_path": str(root),
                        "pattern": pattern,
                        "case_sensitive": case_sensitive,
                        "max_depth": capped_depth,
                        "max_matches": capped_matches,
                        "max_file_bytes": capped_file_bytes,
                        "include_hidden": include_hidden,
                        "files_scanned": files_scanned,
                        "files_skipped_large": files_skipped_large,
                        "files_skipped_binary": files_skipped_binary,
                        "match_count": len(matches),
                        "truncated": truncated,
                        "matches": matches,
                    },
                    operator_note="Bounded grep results returned in machine payload.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                )
            },
        )
    except Exception as exc:
        return RunResult(
            ok=False,
            error=repr(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to grep text",
                    machine_payload={
                        "root_path": str(root),
                        "pattern": pattern,
                        "exception": repr(exc),
                    },
                    operator_note="Inspect file permissions and retry.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
