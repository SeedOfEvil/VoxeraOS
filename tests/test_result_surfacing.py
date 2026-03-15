"""Tests for evidence-grounded result surfacing layer.

Covers:
1. File read completion surfaces file content or bounded excerpt
2. File exists completion surfaces exists/missing clearly
3. File stat completion surfaces key metadata
4. List dir completion surfaces bounded directory listing
5. Service status completion surfaces actual state
6. Recent logs completion surfaces bounded useful log information
7. Diagnostics mission completion surfaces a compact multi-value snapshot
8. Process list surfaces count and top processes
9. Fallback returns None when only thin status is available
10. Boundedness: large outputs are truncated
"""

from __future__ import annotations

from voxera.vera.result_surfacing import (
    RESULT_CLASS_DIAGNOSTICS_SNAPSHOT,
    RESULT_CLASS_EXISTENCE,
    RESULT_CLASS_LIST_DIR,
    RESULT_CLASS_PROCESS_LIST,
    RESULT_CLASS_RECENT_LOGS,
    RESULT_CLASS_SERVICE_STATE,
    RESULT_CLASS_STAT_INFO,
    RESULT_CLASS_TEXT_CONTENT,
    classify_result_family,
    extract_value_forward_text,
)


def _structured_with_step(skill_id: str, machine_payload: dict, summary: str = "") -> dict:
    """Build a minimal structured execution dict with one step."""
    return {
        "step_summaries": [
            {
                "step_index": 1,
                "skill_id": skill_id,
                "status": "succeeded",
                "summary": summary,
                "machine_payload": machine_payload,
            }
        ],
        "latest_summary": summary,
        "terminal_outcome": "succeeded",
    }


# ---------------------------------------------------------------------------
# File read
# ---------------------------------------------------------------------------


def test_file_read_surfaces_path_and_size():
    structured = _structured_with_step(
        "files.read_text",
        {"path": "/home/user/VoxeraOS/notes/todo.txt", "bytes": 42},
        summary="Read text from /home/user/VoxeraOS/notes/todo.txt",
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "42 bytes" in result
    assert "todo.txt" in result


def test_file_read_surfaces_content_excerpt_when_latest_summary_has_content():
    structured = _structured_with_step(
        "files.read_text",
        {"path": "/notes/todo.txt", "bytes": 200},
        summary="Read text from /notes/todo.txt",
    )
    # Simulate latest_summary being richer than just the skill summary
    structured["latest_summary"] = (
        "buy milk\nwalk dog\nfix bug\nclean house\ndo laundry\nmore tasks here"
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "buy milk" in result
    assert "todo.txt" in result


def test_file_read_truncates_large_content():
    structured = _structured_with_step(
        "files.read_text",
        {"path": "/home/user/VoxeraOS/notes/big.txt", "bytes": 10000},
        summary="Read text from /home/user/VoxeraOS/notes/big.txt",
    )
    structured["latest_summary"] = "x" * 1000
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert result.endswith("...")
    assert len(result) < 600


def test_file_read_classifies_as_text_content():
    structured = _structured_with_step(
        "files.read_text",
        {"path": "/home/user/VoxeraOS/notes/todo.txt", "bytes": 42},
    )
    assert classify_result_family(structured=structured) == RESULT_CLASS_TEXT_CONTENT


# ---------------------------------------------------------------------------
# File exists
# ---------------------------------------------------------------------------


def test_file_exists_surfaces_exists():
    structured = _structured_with_step(
        "files.exists",
        {"path": "/home/user/VoxeraOS/notes/foo.txt", "exists": True, "kind": "file"},
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "foo.txt exists" in result
    assert "(file)" in result


def test_file_exists_surfaces_missing():
    structured = _structured_with_step(
        "files.exists",
        {"path": "/home/user/VoxeraOS/notes/bar.txt", "exists": False, "kind": "file"},
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "does not exist" in result


def test_file_exists_classifies_correctly():
    structured = _structured_with_step(
        "files.exists",
        {"path": "/p", "exists": True, "kind": "file"},
    )
    assert classify_result_family(structured=structured) == RESULT_CLASS_EXISTENCE


# ---------------------------------------------------------------------------
# File stat
# ---------------------------------------------------------------------------


def test_file_stat_surfaces_key_metadata():
    structured = _structured_with_step(
        "files.stat",
        {
            "path": "/home/user/VoxeraOS/notes/data.csv",
            "kind": "file",
            "size_bytes": 1024,
            "modified_ts": "2025-01-15T10:30:00+00:00",
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "data.csv" in result
    assert "1024B" in result
    assert "2025-01-15" in result
    assert classify_result_family(structured=structured) == RESULT_CLASS_STAT_INFO


# ---------------------------------------------------------------------------
# List dir
# ---------------------------------------------------------------------------


def test_list_dir_surfaces_entries():
    entries = [
        {"name": "file1.txt", "path": "file1.txt", "is_dir": False, "size_bytes": 100},
        {"name": "file2.txt", "path": "file2.txt", "is_dir": False, "size_bytes": 200},
        {"name": "subdir", "path": "subdir", "is_dir": True, "size_bytes": 0},
    ]
    structured = _structured_with_step(
        "files.list_dir",
        {"path": "/home/user/VoxeraOS/notes", "entries": entries, "entry_count": 3},
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "3 entries" in result
    assert "file1.txt" in result
    assert "subdir" in result
    assert classify_result_family(structured=structured) == RESULT_CLASS_LIST_DIR


# ---------------------------------------------------------------------------
# Service status
# ---------------------------------------------------------------------------


def test_service_status_surfaces_actual_state():
    structured = _structured_with_step(
        "system.service_status",
        {
            "service": "voxera-vera.service",
            "ActiveState": "inactive",
            "SubState": "dead",
            "Id": "voxera-vera.service",
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "voxera-vera.service" in result
    assert "inactive/dead" in result


def test_service_status_classifies_correctly():
    structured = _structured_with_step(
        "system.service_status",
        {"service": "x.service", "ActiveState": "active", "SubState": "running"},
    )
    assert classify_result_family(structured=structured) == RESULT_CLASS_SERVICE_STATE


# ---------------------------------------------------------------------------
# Recent logs
# ---------------------------------------------------------------------------


def test_recent_logs_surfaces_bounded_excerpt():
    log_lines = [
        "2025-01-15T10:30:00 voxera-daemon[123]: Starting service...",
        "2025-01-15T10:30:01 voxera-daemon[123]: Ready.",
    ]
    structured = _structured_with_step(
        "system.recent_service_logs",
        {
            "service": "voxera-daemon.service",
            "line_count": 2,
            "since_minutes": 15,
            "logs": log_lines,
            "truncated": False,
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "voxera-daemon.service" in result
    assert "2 lines" in result
    assert "last 15m" in result
    assert "Starting service..." in result
    assert "Ready." in result


def test_recent_logs_surfaces_count_only_without_log_lines():
    structured = _structured_with_step(
        "system.recent_service_logs",
        {
            "service": "voxera-daemon.service",
            "line_count": 5,
            "since_minutes": 30,
            "logs": [],
            "truncated": False,
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "5 lines" in result
    assert "last 30m" in result


def test_recent_logs_truncation_flag_shown():
    structured = _structured_with_step(
        "system.recent_service_logs",
        {
            "service": "x.service",
            "line_count": 50,
            "since_minutes": 15,
            "logs": [f"line {i}" for i in range(50)],
            "truncated": True,
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "[truncated]" in result


def test_recent_logs_classifies_correctly():
    structured = _structured_with_step(
        "system.recent_service_logs",
        {"service": "x.service", "line_count": 1, "since_minutes": 5, "logs": []},
    )
    assert classify_result_family(structured=structured) == RESULT_CLASS_RECENT_LOGS


# ---------------------------------------------------------------------------
# Diagnostics snapshot
# ---------------------------------------------------------------------------


def test_diagnostics_snapshot_surfaces_compact_values():
    structured = {
        "step_summaries": [
            {
                "step_index": 1,
                "skill_id": "system.host_info",
                "status": "succeeded",
                "summary": "",
                "machine_payload": {"hostname": "voxera-box", "uptime_seconds": 7200},
            },
            {
                "step_index": 2,
                "skill_id": "system.memory_usage",
                "status": "succeeded",
                "summary": "",
                "machine_payload": {"used_gib": 4.2, "total_gib": 16.0, "used_percent": 26.3},
            },
            {
                "step_index": 3,
                "skill_id": "system.load_snapshot",
                "status": "succeeded",
                "summary": "",
                "machine_payload": {"load_1m": 0.5, "load_5m": 0.3, "load_15m": 0.2},
            },
            {
                "step_index": 4,
                "skill_id": "system.disk_usage",
                "status": "succeeded",
                "summary": "",
                "machine_payload": {"used_percent": 45, "free_gb": 120},
            },
        ],
        "terminal_outcome": "succeeded",
    }
    result = extract_value_forward_text(structured=structured, mission_id="system_diagnostics")
    assert result is not None
    assert "Diagnostics snapshot:" in result
    assert "host=voxera-box" in result
    assert "uptime=" in result
    assert "memory=4.2/16.0GiB" in result
    assert "load(1/5/15m)=0.5/0.3/0.2" in result
    assert "disk=45%" in result
    assert "120GB free" in result


def test_diagnostics_classifies_correctly():
    structured = {"step_summaries": [], "terminal_outcome": "succeeded"}
    assert (
        classify_result_family(structured=structured, mission_id="system_diagnostics")
        == RESULT_CLASS_DIAGNOSTICS_SNAPSHOT
    )


def test_diagnostics_not_triggered_without_mission_id():
    structured = {
        "step_summaries": [
            {
                "step_index": 1,
                "skill_id": "system.host_info",
                "status": "succeeded",
                "machine_payload": {"hostname": "h"},
            }
        ],
    }
    # Without mission_id=system_diagnostics, diagnostics snapshot should not fire,
    # but no other extractor should match either if no specific skill matches.
    result = extract_value_forward_text(structured=structured, mission_id="")
    assert result is None


# ---------------------------------------------------------------------------
# Process list
# ---------------------------------------------------------------------------


def test_process_list_surfaces_top_processes():
    processes = [
        {"name": "systemd", "pid": 1},
        {"name": "voxera-daemon", "pid": 123},
        {"name": "python3", "pid": 456},
    ]
    structured = _structured_with_step(
        "system.process_list",
        {"processes": processes, "count": 3},
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "3 running processes" in result
    assert "systemd" in result
    assert "voxera-daemon" in result
    assert classify_result_family(structured=structured) == RESULT_CLASS_PROCESS_LIST


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def test_fallback_returns_none_when_no_useful_value():
    structured = {
        "step_summaries": [
            {
                "step_index": 1,
                "skill_id": "files.write_text",
                "status": "succeeded",
                "summary": "Wrote file",
                "machine_payload": {"path": "/notes/x.txt"},
            }
        ],
        "latest_summary": "Wrote file",
        "terminal_outcome": "succeeded",
    }
    result = extract_value_forward_text(structured=structured)
    assert result is None


def test_fallback_returns_none_for_empty_structured():
    result = extract_value_forward_text(structured={})
    assert result is None


def test_fallback_returns_none_for_no_step_summaries():
    result = extract_value_forward_text(structured={"terminal_outcome": "succeeded"})
    assert result is None


# ---------------------------------------------------------------------------
# Boundedness
# ---------------------------------------------------------------------------


def test_log_excerpt_bounded_to_max_lines():
    log_lines = [f"log line {i}" for i in range(100)]
    structured = _structured_with_step(
        "system.recent_service_logs",
        {
            "service": "x.service",
            "line_count": 100,
            "since_minutes": 15,
            "logs": log_lines,
            "truncated": True,
        },
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    # Should only show last 8 lines, not all 100
    assert "log line 92" in result
    assert "log line 99" in result
    # First lines should not appear
    assert "log line 0\n" not in result


def test_list_dir_bounded_to_max_entries():
    entries = [
        {"name": f"file{i}.txt", "path": f"file{i}.txt", "is_dir": False, "size_bytes": i}
        for i in range(20)
    ]
    structured = _structured_with_step(
        "files.list_dir",
        {"path": "/notes", "entries": entries, "entry_count": 20},
    )
    result = extract_value_forward_text(structured=structured)
    assert result is not None
    assert "20 entries" in result
    assert "+8 more" in result  # 20 - 12 shown = 8 more
