from __future__ import annotations

import json
import subprocess

from voxera.core.missions import MissionRunner, get_mission, list_missions
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera.skills.runner import SkillRunner, is_skill_read_only
from voxera_builtin_skills import recent_service_logs, service_status


def test_diagnostics_mission_exists_and_is_read_only():
    mission_ids = {m.id for m in list_missions()}
    assert "system_diagnostics" in mission_ids

    reg = SkillRegistry()
    reg.discover()
    mission = get_mission("system_diagnostics")
    assert len(mission.steps) == 5
    assert [step.skill_id for step in mission.steps] == [
        "system.host_info",
        "system.memory_usage",
        "system.load_snapshot",
        "system.disk_usage",
        "system.process_list",
    ]
    for step in mission.steps:
        assert is_skill_read_only(reg.get(step.skill_id)), step.skill_id


def test_service_status_rejects_invalid_service_name_fail_closed():
    rr = service_status.run("../../etc/passwd")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["blocked"] is True
    assert payload["error_class"] == "invalid_input"


def test_recent_service_logs_rejects_unsafe_query_bounds_fail_closed():
    rr = recent_service_logs.run("voxera-daemon.service", lines=9999)
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["blocked"] is True
    assert payload["error_class"] == "invalid_input"


def test_service_status_happy_path_with_mocked_systemctl(monkeypatch):
    def _fake_run(cmd, **_kwargs):
        # Both system and user scopes return active/running
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=(
                "Id=voxera-daemon.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = service_status.run("voxera-daemon.service")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["machine_payload"]["ActiveState"] == "active"
    assert payload["machine_payload"]["scope"] in {"user", "system"}


def test_service_status_user_scope_preferred_when_active(monkeypatch):
    """When user scope shows active but system scope shows inactive, user scope wins."""
    call_count = 0

    def _fake_run(cmd, **_kwargs):
        nonlocal call_count
        call_count += 1
        is_user = "--user" in cmd
        if is_user:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=(
                    "Id=voxera-vera.service\n"
                    "LoadState=loaded\n"
                    "ActiveState=active\n"
                    "SubState=running\n"
                    "UnitFileState=enabled\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=(
                "Id=voxera-vera.service\n"
                "LoadState=not-found\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "UnitFileState=\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = service_status.run("voxera-vera.service")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["scope"] == "user"
    assert payload["ActiveState"] == "active"
    assert payload["SubState"] == "running"
    assert payload.get("other_scope") == "system"
    assert payload.get("other_ActiveState") == "inactive"


def test_service_status_system_failed_preferred_over_user_inactive(monkeypatch):
    """When system scope reports failed and user scope is inactive, system scope is primary."""

    def _fake_run(cmd, **_kwargs):
        is_user = "--user" in cmd
        if is_user:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=(
                    "Id=my.service\n"
                    "LoadState=not-found\n"
                    "ActiveState=inactive\n"
                    "SubState=dead\n"
                    "UnitFileState=\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=(
                "Id=my.service\n"
                "LoadState=loaded\n"
                "ActiveState=failed\n"
                "SubState=failed\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = service_status.run("my.service")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    # System scope should be primary because failed is noteworthy
    assert payload["scope"] == "system"
    assert payload["ActiveState"] == "failed"
    # Cross-scope context should be surfaced
    assert payload.get("other_scope") == "user"
    assert payload.get("other_ActiveState") == "inactive"


def test_recent_service_logs_timeout_error_class(monkeypatch):
    """When both journalctl scopes time out, error_class is timeout, not missing_dependency."""

    def _fake_run(cmd, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines=10, since_minutes=5)
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "timeout"
    assert payload["retryable"] is True


def test_recent_service_logs_query_failed_error_class(monkeypatch):
    """When both journalctl scopes fail with CalledProcessError, error_class is service_log_query_failed."""

    def _fake_run(cmd, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, output="", stderr="Permission denied"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines=10, since_minutes=5)
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "service_log_query_failed"
    assert payload["retryable"] is False


def test_recent_service_logs_default_args(monkeypatch):
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):
        captured_cmds.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="2026-01-01T00:00:01Z line one\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["lines_requested"] == 50
    assert payload["since_minutes"] == 15
    assert payload["scope"] in {"user", "system"}
    # At least one call should have -n 50
    assert any("-n" in cmd and "50" in cmd for cmd in captured_cmds)


def test_recent_service_logs_happy_path_with_mocked_journalctl(monkeypatch):
    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="2026-01-01T00:00:01Z line one\n2026-01-01T00:00:02Z line two\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines=10, since_minutes=5)
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["line_count"] == 2
    assert payload["service"] == "voxera-daemon.service"
    assert payload["scope"] in {"user", "system"}


def test_recent_service_logs_accepts_string_numeric_args(monkeypatch):
    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="2026-01-01T00:00:01Z line one\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines="20", since_minutes="30")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["lines_requested"] == 20
    assert payload["since_minutes"] == 30


def test_recent_service_logs_prefers_user_scope_with_content(monkeypatch):
    """When user scope has logs but system scope is empty, user scope is preferred."""

    def _fake_run(cmd, **_kwargs):
        is_user = "--user-unit" in cmd
        if is_user:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="2026-01-01T00:00:01Z user log line\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines=10, since_minutes=5)
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["scope"] == "user"
    assert payload["line_count"] == 1
    assert "user log line" in payload["logs"][0]


def test_recent_service_logs_no_entries_message(monkeypatch):
    """When both scopes return empty, summary says no recent logs."""

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="-- No entries --\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines=10, since_minutes=5)
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["line_count"] == 0
    assert "No recent logs" in rr.output


def test_recent_service_logs_rejects_invalid_numeric_strings(monkeypatch):
    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("journalctl should not be invoked for invalid numeric strings")

    monkeypatch.setattr(subprocess, "run", _should_not_run)
    rr = recent_service_logs.run("voxera-daemon.service", lines="abc", since_minutes="1")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["blocked"] is True
    assert payload["error_class"] == "invalid_input"


def test_recent_service_logs_rejects_invalid_service_name(monkeypatch):
    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("journalctl should not be invoked for invalid service name")

    monkeypatch.setattr(subprocess, "run", _should_not_run)
    rr = recent_service_logs.run("../../etc/passwd", lines="20", since_minutes="30")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["blocked"] is True
    assert payload["error_class"] == "invalid_input"


def test_diagnostics_mission_executes_through_queue_and_writes_evidence(tmp_path):
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-diagnostics.json").write_text(
        json.dumps({"mission_id": "system_diagnostics"}),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    art = queue_root / "artifacts" / "job-diagnostics"
    execution_result = json.loads((art / "execution_result.json").read_text(encoding="utf-8"))
    step_results = json.loads((art / "step_results.json").read_text(encoding="utf-8"))

    assert execution_result["review_summary"]["job_id"] == "job-diagnostics.json"
    assert execution_result["review_summary"]["terminal_outcome"] in {"succeeded", "failed"}
    assert len(step_results) == 5
    assert all(item["skill_id"].startswith("system.") for item in step_results)


def test_simulate_system_diagnostics_requires_no_approvals():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals())

    sim = mission_runner.simulate(get_mission("system_diagnostics"))

    assert sim.blocked is False
    assert sim.approvals_required == 0
    assert len(sim.steps) == 5
