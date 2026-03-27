from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"
_MAX_ENTRIES_CAP = 2000


def _resolve_existing_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def run(
    root_path: str,
    max_depth: int = 4,
    include_hidden: bool = False,
    max_entries: int = 400,
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
                    machine_payload={"root_path": root_path},
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
                    summary="Rejected list_tree outside allowlist",
                    machine_payload={"root_path": root_path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Tree root must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=exc.error_class == "path_blocked_scope",
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    if not root.is_dir():
        return RunResult(
            ok=False,
            error=f"Root path is not a directory: {root}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected non-directory root path",
                    machine_payload={"root_path": str(root)},
                    operator_note="Provide a directory root path for files.list_tree.",
                    next_action_hint="provide_directory_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Root path is not a directory: {root}",
                    error_class="invalid_input",
                )
            },
        )

    capped_depth = max(0, min(max_depth, 32))
    capped_entries = max(1, min(max_entries, _MAX_ENTRIES_CAP))
    entries: list[dict[str, object]] = []
    directory_count = 0
    file_count = 0
    truncated = False

    try:
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            relative = current.relative_to(ALLOWED_ROOT.expanduser().resolve(strict=False))
            is_dir = current.is_dir()
            if is_dir:
                directory_count += 1
            else:
                file_count += 1

            entries.append(
                {
                    "path": str(relative),
                    "name": current.name,
                    "depth": depth,
                    "type": "directory" if is_dir else "file",
                }
            )
            if len(entries) >= capped_entries:
                truncated = True
                break

            if not is_dir or depth >= capped_depth:
                continue
            children = sorted(current.iterdir(), key=lambda item: item.name.lower(), reverse=True)
            for child in children:
                if not include_hidden and child.name.startswith("."):
                    continue
                stack.append((child, depth + 1))

        summary = f"Listed tree with {len(entries)} entries under {root}"
        if truncated:
            summary = f"Listed tree with {len(entries)} entries under {root} (truncated)"

        return RunResult(
            ok=True,
            output=summary,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=summary,
                    machine_payload={
                        "root_path": str(root),
                        "max_depth": capped_depth,
                        "max_entries": capped_entries,
                        "include_hidden": include_hidden,
                        "entry_count": len(entries),
                        "directory_count": directory_count,
                        "file_count": file_count,
                        "truncated": truncated,
                        "entries": entries,
                    },
                    operator_note="Bounded tree listing returned in machine payload.",
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
                    summary="Failed to list tree",
                    machine_payload={"root_path": str(root), "exception": repr(exc)},
                    operator_note="Inspect directory permissions and retry.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
