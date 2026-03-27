from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"
_MAX_RESULTS_CAP = 1000


def _resolve_existing_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def _walk_paths(*, root: Path, include_hidden: bool, max_depth: int):
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth or not current.is_dir():
            continue
        children = sorted(current.iterdir(), key=lambda item: item.name.lower(), reverse=True)
        for child in children:
            if not include_hidden and child.name.startswith("."):
                continue
            stack.append((child, depth + 1))


def run(
    root_path: str,
    glob: str = "*",
    name_contains: str | None = None,
    max_depth: int = 8,
    include_hidden: bool = False,
    max_results: int = 200,
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
                    operator_note="Provide an existing directory path under notes scope.",
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
                    summary="Rejected find outside allowlist",
                    machine_payload={"root_path": root_path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Find root must stay within allowed notes directory.",
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
                    operator_note="Provide a directory root path for files.find.",
                    next_action_hint="provide_directory_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Root path is not a directory: {root}",
                    error_class="invalid_input",
                )
            },
        )

    normalized_contains = (name_contains or "").strip().lower()
    capped_results = max(1, min(max_results, _MAX_RESULTS_CAP))
    capped_depth = max(0, min(max_depth, 32))

    results: list[dict[str, object]] = []
    scanned = 0
    truncated = False

    try:
        for candidate, depth in _walk_paths(
            root=root, include_hidden=include_hidden, max_depth=capped_depth
        ):
            if candidate == root:
                continue
            scanned += 1
            if not candidate.match(glob):
                continue
            if normalized_contains and normalized_contains not in candidate.name.lower():
                continue
            relative = candidate.relative_to(ALLOWED_ROOT.expanduser().resolve(strict=False))
            results.append(
                {
                    "path": str(relative),
                    "name": candidate.name,
                    "is_dir": candidate.is_dir(),
                    "depth": depth,
                }
            )
            if len(results) >= capped_results:
                truncated = True
                break

        summary = f"Found {len(results)} paths under {root}"
        if truncated:
            summary = f"Found {len(results)} paths under {root} (truncated)"

        return RunResult(
            ok=True,
            output=summary,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=summary,
                    machine_payload={
                        "root_path": str(root),
                        "glob": glob,
                        "name_contains": name_contains,
                        "max_depth": capped_depth,
                        "max_results": capped_results,
                        "include_hidden": include_hidden,
                        "scanned_paths": scanned,
                        "result_count": len(results),
                        "truncated": truncated,
                        "results": results,
                    },
                    operator_note="Bounded recursive find results returned in machine payload.",
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
                    summary="Failed to find paths",
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
