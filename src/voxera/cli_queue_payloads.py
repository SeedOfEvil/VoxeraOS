from __future__ import annotations

from typing import Any


def build_files_queue_payload(
    *,
    action: str,
    step_skill_id: str,
    step_args: dict[str, Any],
) -> dict[str, Any]:
    return {
        "goal": f"files:{action}",
        "steps": [{"skill_id": step_skill_id, "args": step_args}],
        "notes": "Queued via voxera queue files helper.",
    }


def build_files_find_args(
    *,
    root_path: str,
    glob: str,
    name_contains: str | None,
    max_depth: int,
    include_hidden: bool,
    max_results: int,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "root_path": root_path,
        "glob": glob,
        "max_depth": max_depth,
        "include_hidden": include_hidden,
        "max_results": max_results,
    }
    if name_contains is not None:
        args["name_contains"] = name_contains
    return args


def build_files_grep_text_args(
    *,
    root_path: str,
    pattern: str,
    case_sensitive: bool,
    max_depth: int,
    include_hidden: bool,
    max_matches: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    return {
        "root_path": root_path,
        "pattern": pattern,
        "case_sensitive": case_sensitive,
        "max_depth": max_depth,
        "include_hidden": include_hidden,
        "max_matches": max_matches,
        "max_file_bytes": max_file_bytes,
    }


def build_files_list_tree_args(
    *,
    root_path: str,
    max_depth: int,
    include_hidden: bool,
    max_entries: int,
) -> dict[str, Any]:
    return {
        "root_path": root_path,
        "max_depth": max_depth,
        "include_hidden": include_hidden,
        "max_entries": max_entries,
    }


def build_files_copy_move_args(
    *,
    source_path: str,
    destination_path: str,
    overwrite: bool,
) -> dict[str, Any]:
    return {
        "source_path": source_path,
        "destination_path": destination_path,
        "overwrite": overwrite,
    }


def build_files_rename_args(*, path: str, new_name: str, overwrite: bool) -> dict[str, Any]:
    return {
        "path": path,
        "new_name": new_name,
        "overwrite": overwrite,
    }


def build_health_reset_event_name(
    *,
    scope: str,
    counter_group: str | None,
    event_by_scope: dict[str, str],
) -> str:
    if counter_group:
        return "health_reset_historical_counters"
    return event_by_scope.get(scope, "health_reset")


def build_health_reset_log_payload(
    *,
    event_name: str,
    scope: str,
    counter_group: str | None,
    changed_fields: list[str],
    timestamp_ms: int,
) -> dict[str, Any]:
    return {
        "event": event_name,
        "scope": scope,
        "counter_group": counter_group,
        "actor_surface": "cli",
        "fields_changed": changed_fields,
        "timestamp_ms": timestamp_ms,
    }
