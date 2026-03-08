"""Tests for deterministic simple-intent routing and fail-closed mismatch detection.

Coverage targets:
1. Recognised open intent routes only to open-family
2. Recognised write intent routes only to write-family
3. Recognised read intent routes only to read-family
4. Recognised advisory/status intent stays read-only
5. Ambiguous request does not get forced into wrong direct route
6. Planner mismatch yields deterministic canonical failure artifact
7. Queue / CLI / panel surfaces stay coherent
8. Regression: "open terminal" must not become write_text
9. Regression: "write file" must not become open_app/open_url
10. Regression: advisory request must not drift into mutating skill
11. Mismatch is blocked before side effects occur
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from voxera.core.simple_intent import (
    INTENT_ALLOWED_SKILLS,
    SimpleIntentResult,
    check_skill_family_mismatch,
    classify_simple_operator_intent,
)

# ---------------------------------------------------------------------------
# Unit tests: classify_simple_operator_intent
# ---------------------------------------------------------------------------


class TestClassifyAssistantQuestion:
    def test_what_is_system_status(self):
        result = classify_simple_operator_intent(goal="what is my current system status?")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True
        assert result.fail_closed is True
        assert "system.status" in result.allowed_skill_ids

    def test_what_is_short_phrase(self):
        result = classify_simple_operator_intent(goal="what is the system status?")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True

    def test_what_are(self):
        result = classify_simple_operator_intent(goal="what are the active jobs?")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True

    def test_tell_me(self):
        result = classify_simple_operator_intent(goal="tell me the current status")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True

    def test_status_phrase_short(self):
        result = classify_simple_operator_intent(goal="current system status")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True

    def test_how_do(self):
        result = classify_simple_operator_intent(goal="how do I check health?")
        assert result.intent_kind == "assistant_question"
        assert result.deterministic is True

    def test_advisory_does_not_allow_mutating_skill(self):
        result = classify_simple_operator_intent(goal="what is system status?")
        # Must NOT allow write or open skills
        assert "files.write_text" not in result.allowed_skill_ids
        assert "system.open_app" not in result.allowed_skill_ids
        assert "system.open_url" not in result.allowed_skill_ids
        assert "sandbox.exec" not in result.allowed_skill_ids


class TestClassifyOpenResource:
    def test_open_terminal(self):
        result = classify_simple_operator_intent(goal="open terminal")
        assert result.intent_kind == "open_resource"
        assert result.deterministic is True
        assert result.fail_closed is True
        # Both system.open_app (e.g. gnome-terminal) and system.terminal_run_once
        # (deterministic demo skill) are valid first steps for "open terminal".
        assert "system.open_app" in result.allowed_skill_ids
        assert "system.terminal_run_once" in result.allowed_skill_ids

    def test_open_firefox(self):
        result = classify_simple_operator_intent(goal="open Firefox")
        assert result.intent_kind == "open_resource"
        assert result.deterministic is True

    def test_launch_app(self):
        result = classify_simple_operator_intent(goal="launch terminal")
        assert result.intent_kind == "open_resource"
        assert result.deterministic is True

    def test_start_app(self):
        result = classify_simple_operator_intent(goal="start firefox")
        assert result.intent_kind == "open_resource"
        assert result.deterministic is True

    def test_open_url(self):
        result = classify_simple_operator_intent(goal="open https://example.com")
        assert result.intent_kind == "open_resource"
        assert result.deterministic is True
        assert "system.open_url" in result.allowed_skill_ids

    def test_open_resource_does_not_allow_write(self):
        result = classify_simple_operator_intent(goal="open terminal")
        assert "files.write_text" not in result.allowed_skill_ids
        assert "files.read_text" not in result.allowed_skill_ids


class TestClassifyWriteFile:
    def test_write_with_filename(self):
        result = classify_simple_operator_intent(goal="write text.txt with hello world")
        assert result.intent_kind == "write_file"
        assert result.deterministic is True
        assert result.fail_closed is True
        assert "files.write_text" in result.allowed_skill_ids

    def test_write_path(self):
        result = classify_simple_operator_intent(goal="write ~/notes/foo.txt with content")
        assert result.intent_kind == "write_file"
        assert result.deterministic is True

    def test_append_to_file(self):
        result = classify_simple_operator_intent(goal="append to ~/VoxeraOS/notes/log.txt")
        assert result.intent_kind == "write_file"
        assert result.deterministic is True

    def test_create_file(self):
        result = classify_simple_operator_intent(goal="create file notes.txt with hello")
        assert result.intent_kind == "write_file"
        assert result.deterministic is True

    def test_write_does_not_allow_open_skills(self):
        result = classify_simple_operator_intent(goal="write text.txt with hello world")
        assert "system.open_app" not in result.allowed_skill_ids
        assert "system.open_url" not in result.allowed_skill_ids
        assert "sandbox.exec" not in result.allowed_skill_ids


class TestClassifyReadFile:
    def test_read_path(self):
        result = classify_simple_operator_intent(goal="read ~/VoxeraOS/notes/foo.txt")
        assert result.intent_kind == "read_file"
        assert result.deterministic is True
        assert result.fail_closed is True
        assert "files.read_text" in result.allowed_skill_ids

    def test_read_absolute_path(self):
        result = classify_simple_operator_intent(goal="read /home/user/notes.txt")
        assert result.intent_kind == "read_file"
        assert result.deterministic is True

    def test_cat_path(self):
        result = classify_simple_operator_intent(goal="cat ~/notes.txt")
        assert result.intent_kind == "read_file"
        assert result.deterministic is True

    def test_display_path(self):
        result = classify_simple_operator_intent(goal="display ~/VoxeraOS/notes/foo.txt")
        assert result.intent_kind == "read_file"
        assert result.deterministic is True

    def test_show_contents_of_path(self):
        result = classify_simple_operator_intent(goal="show contents of ~/notes/log.txt")
        assert result.intent_kind == "read_file"
        assert result.deterministic is True

    def test_read_does_not_allow_write(self):
        result = classify_simple_operator_intent(goal="read ~/VoxeraOS/notes/foo.txt")
        assert "files.write_text" not in result.allowed_skill_ids
        assert "system.open_app" not in result.allowed_skill_ids


class TestClassifyRunCommand:
    def test_run_command_literal(self):
        result = classify_simple_operator_intent(goal="run command `ls -la`")
        assert result.intent_kind == "run_command"
        assert result.deterministic is True
        assert result.fail_closed is True
        assert "sandbox.exec" in result.allowed_skill_ids

    def test_execute(self):
        result = classify_simple_operator_intent(goal="execute ls -la")
        assert result.intent_kind == "run_command"
        assert result.deterministic is True

    def test_exec(self):
        result = classify_simple_operator_intent(goal="exec ls")
        assert result.intent_kind == "run_command"
        assert result.deterministic is True


class TestClassifyUnknown:
    def test_ambiguous_handle_this(self):
        result = classify_simple_operator_intent(goal="handle this for me")
        assert result.intent_kind == "unknown_or_ambiguous"
        assert result.deterministic is False
        assert result.fail_closed is False
        assert len(result.allowed_skill_ids) == 0

    def test_empty_goal_is_unknown(self):
        result = classify_simple_operator_intent(goal="")
        assert result.intent_kind == "unknown_or_ambiguous"
        assert result.deterministic is False

    def test_none_goal_is_unknown(self):
        result = classify_simple_operator_intent(goal=None)
        assert result.intent_kind == "unknown_or_ambiguous"
        assert result.deterministic is False

    def test_vague_goal(self):
        result = classify_simple_operator_intent(goal="do the daily thing")
        assert result.intent_kind == "unknown_or_ambiguous"

    def test_complex_multi_step_goal(self):
        result = classify_simple_operator_intent(
            goal="open terminal and write a file and check status"
        )
        # Multi-step ambiguous; open_terminal match fires first – that's fine, it's
        # deterministic and will constrain to open-family. But let's just check it's
        # not unknown – the classifier fired on "open terminal".
        # (We accept either open_resource or unknown_or_ambiguous here; the key
        # constraint is that it must NOT route to write_file or read_file.)
        assert result.intent_kind not in {"write_file", "read_file", "run_command"}

    def test_read_without_path_is_unknown(self):
        # "read" without a path should NOT be classified as read_file
        result = classify_simple_operator_intent(goal="read the situation carefully")
        assert result.intent_kind == "unknown_or_ambiguous"

    def test_open_with_path_like_is_unknown(self):
        # "open ~/notes/foo.txt" has a path-like char – should NOT be open_resource
        result = classify_simple_operator_intent(goal="open ~/notes/foo.txt")
        assert result.intent_kind == "unknown_or_ambiguous"


# ---------------------------------------------------------------------------
# Unit tests: check_skill_family_mismatch
# ---------------------------------------------------------------------------


class TestCheckSkillFamilyMismatch:
    def _open_intent(self) -> SimpleIntentResult:
        return classify_simple_operator_intent(goal="open terminal")

    def _write_intent(self) -> SimpleIntentResult:
        return classify_simple_operator_intent(goal="write text.txt with hello world")

    def _read_intent(self) -> SimpleIntentResult:
        return classify_simple_operator_intent(goal="read ~/VoxeraOS/notes/foo.txt")

    def _advisory_intent(self) -> SimpleIntentResult:
        return classify_simple_operator_intent(goal="what is system status?")

    def _unknown_intent(self) -> SimpleIntentResult:
        return classify_simple_operator_intent(goal="handle this for me")

    # --- No mismatch (correct routing) ---

    def test_open_intent_system_open_app_no_mismatch(self):
        mismatch, _ = check_skill_family_mismatch(self._open_intent(), "system.open_app")
        assert mismatch is False

    def test_open_url_intent_system_open_url_no_mismatch(self):
        # "open terminal" uses _TERMINAL_OPEN_SKILLS (no open_url); use a URL goal instead.
        url_intent = classify_simple_operator_intent(goal="open https://example.com")
        mismatch, _ = check_skill_family_mismatch(url_intent, "system.open_url")
        assert mismatch is False

    def test_write_intent_files_write_text_no_mismatch(self):
        mismatch, _ = check_skill_family_mismatch(self._write_intent(), "files.write_text")
        assert mismatch is False

    def test_read_intent_files_read_text_no_mismatch(self):
        mismatch, _ = check_skill_family_mismatch(self._read_intent(), "files.read_text")
        assert mismatch is False

    def test_advisory_intent_system_status_no_mismatch(self):
        mismatch, _ = check_skill_family_mismatch(self._advisory_intent(), "system.status")
        assert mismatch is False

    def test_advisory_intent_assistant_advisory_no_mismatch(self):
        mismatch, _ = check_skill_family_mismatch(self._advisory_intent(), "assistant.advisory")
        assert mismatch is False

    # --- Mismatch detected (wrong routing) ---

    def test_open_terminal_terminal_run_once_no_mismatch(self):
        """Regression: 'open terminal' must accept system.terminal_run_once as first step."""
        intent = classify_simple_operator_intent(goal="open terminal")
        mismatch, _ = check_skill_family_mismatch(intent, "system.terminal_run_once")
        assert mismatch is False

    def test_read_intent_clipboard_copy_is_mismatch(self):
        """Regression: 'read file' must not accept clipboard.copy as first step.

        The planner safety rewrite may convert sandbox.exec steps to clipboard.copy
        for non-explicit goals; this must be rejected fail-closed for read_file routes.
        """
        mismatch, reason = check_skill_family_mismatch(self._read_intent(), "clipboard.copy")
        assert mismatch is True
        assert reason == "simple_intent_skill_family_mismatch"

    def test_open_intent_write_text_is_mismatch(self):
        """Regression: 'open terminal' must not become 'write hello world to a file'."""
        mismatch, reason = check_skill_family_mismatch(self._open_intent(), "files.write_text")
        assert mismatch is True
        assert reason == "simple_intent_skill_family_mismatch"

    def test_write_intent_open_app_is_mismatch(self):
        """Regression: 'write file' must not become 'open_app'."""
        mismatch, reason = check_skill_family_mismatch(self._write_intent(), "system.open_app")
        assert mismatch is True

    def test_write_intent_open_url_is_mismatch(self):
        """Regression: 'write file' must not become 'open_url'."""
        mismatch, reason = check_skill_family_mismatch(self._write_intent(), "system.open_url")
        assert mismatch is True

    def test_advisory_intent_write_text_is_mismatch(self):
        """Regression: advisory request must not drift into mutating skill."""
        mismatch, reason = check_skill_family_mismatch(self._advisory_intent(), "files.write_text")
        assert mismatch is True

    def test_advisory_intent_open_app_is_mismatch(self):
        mismatch, reason = check_skill_family_mismatch(self._advisory_intent(), "system.open_app")
        assert mismatch is True

    def test_read_intent_write_text_is_mismatch(self):
        """Regression: 'read file' must not become 'write'."""
        mismatch, reason = check_skill_family_mismatch(self._read_intent(), "files.write_text")
        assert mismatch is True

    # --- Unknown/ambiguous never mismatches ---

    def test_unknown_intent_never_mismatches(self):
        """Ambiguous request must not be forced into any direct route."""
        intent = self._unknown_intent()
        for skill in ["system.open_app", "files.write_text", "files.read_text", "sandbox.exec"]:
            mismatch, _ = check_skill_family_mismatch(intent, skill)
            assert mismatch is False, f"unknown intent should not mismatch for {skill}"


# ---------------------------------------------------------------------------
# Integration tests: intent routing through the queue daemon
# ---------------------------------------------------------------------------


def _force_policy_ask(monkeypatch: Any) -> None:
    from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig

    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def _stub_plan_with_skill(monkeypatch: Any, *, skill_id: str, args: dict | None = None) -> None:
    from voxera.core.missions import MissionStep, MissionTemplate

    step_args = args or {}

    async def _fake_plan(goal, cfg, registry, source="cli", job_ref=None, **_kwargs):
        return MissionTemplate(
            id="stub_planned",
            title="Stub Plan",
            goal=goal,
            steps=[MissionStep(skill_id=skill_id, args=step_args)],
            notes="stub",
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _fake_plan)


def _make_inbox_job(queue_root: Path, name: str, payload: dict[str, Any]) -> Path:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / f"{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _read_artifact(queue_root: Path, job_name: str, artifact: str) -> dict[str, Any]:
    return json.loads((queue_root / "artifacts" / job_name / artifact).read_text(encoding="utf-8"))


# --- Test 1: Open intent routes only to open-family ---


def test_open_intent_routes_to_open_family_and_succeeds(tmp_path: Path, monkeypatch: Any):
    """'open terminal' with system.open_app first step should succeed.

    Note: system.open_app requires a valid app name in snapshot validation,
    so we use 'gnome-terminal' (an allowlisted app) in the stub args.
    """
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="system.open_app", args={"name": "gnome-terminal"})

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-open", {"goal": "open terminal"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="opened",
        data={
            "results": [
                {
                    "step": 1,
                    "skill": "system.open_app",
                    "args": {"name": "gnome-terminal"},
                    "ok": True,
                }
            ],
            "step_outcomes": [{"step": 1, "skill": "system.open_app", "outcome": "succeeded"}],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    assert (queue_root / "done" / "job-open.json").exists()
    result = _read_artifact(queue_root, "job-open", "execution_result.json")
    assert result["ok"] is True
    assert result["terminal_outcome"] == "succeeded"


def test_open_intent_routes_to_open_family_has_intent_route_in_envelope(
    tmp_path: Path, monkeypatch: Any
):
    """Envelope should carry simple_intent when intent is recognised."""
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="system.open_app", args={"name": "gnome-terminal"})

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-open-envelope", {"goal": "open terminal"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="ok",
        data={
            "results": [],
            "step_outcomes": [],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    envelope = _read_artifact(queue_root, "job-open-envelope", "execution_envelope.json")
    si = envelope["request"].get("simple_intent")
    assert si is not None
    assert si["intent_kind"] == "open_resource"
    assert si["deterministic"] is True
    assert "system.open_app" in si["allowed_skill_ids"]


# --- Test 2: Write intent routes only to write-family ---


def test_write_intent_routes_to_write_family_and_succeeds(tmp_path: Path, monkeypatch: Any):
    """'write text.txt with hello world' with files.write_text first step should succeed."""
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="files.write_text")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-write", {"goal": "write text.txt with hello world"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="written",
        data={
            "results": [{"step": 1, "skill": "files.write_text", "args": {}, "ok": True}],
            "step_outcomes": [{"step": 1, "skill": "files.write_text", "outcome": "succeeded"}],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    assert (queue_root / "done" / "job-write.json").exists()
    result = _read_artifact(queue_root, "job-write", "execution_result.json")
    assert result["ok"] is True


# --- Test 3: Read intent routes only to read-family ---


def test_read_intent_routes_to_read_family_and_succeeds(tmp_path: Path, monkeypatch: Any):
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="files.read_text")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-read", {"goal": "read ~/VoxeraOS/notes/foo.txt"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="content",
        data={
            "results": [{"step": 1, "skill": "files.read_text", "args": {}, "ok": True}],
            "step_outcomes": [{"step": 1, "skill": "files.read_text", "outcome": "succeeded"}],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    assert (queue_root / "done" / "job-read.json").exists()


# --- Test 4: Advisory/status intent stays read-only ---


def test_advisory_intent_routes_to_status_and_succeeds(tmp_path: Path, monkeypatch: Any):
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="system.status")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-status", {"goal": "what is my current system status?"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="ok",
        data={
            "results": [{"step": 1, "skill": "system.status", "args": {}, "ok": True}],
            "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "succeeded"}],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    assert (queue_root / "done" / "job-status.json").exists()
    result = _read_artifact(queue_root, "job-status", "execution_result.json")
    assert result["ok"] is True


# --- Test 5: Ambiguous request does not get forced ---


def test_ambiguous_request_not_forced_into_wrong_route(tmp_path: Path, monkeypatch: Any):
    """'handle this for me' (ambiguous) should go to normal planning without mismatch."""
    _force_policy_ask(monkeypatch)
    # Use system.status (no app-name validation needed) as the planner output
    _stub_plan_with_skill(monkeypatch, skill_id="system.status")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-ambig", {"goal": "handle this for me"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="ok",
        data={
            "results": [{"step": 1, "skill": "system.status", "args": {}, "ok": True}],
            "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "succeeded"}],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    # Ambiguous goal should pass through – no mismatch failure
    assert (queue_root / "done" / "job-ambig.json").exists()
    result = _read_artifact(queue_root, "job-ambig", "execution_result.json")
    assert result["ok"] is True
    # intent_route in execution_result should reflect unknown_or_ambiguous (no constraint)
    ir = result.get("intent_route")
    if ir is not None:
        assert ir.get("intent_kind") == "unknown_or_ambiguous"
        assert ir.get("fail_closed") is False


# --- Test 6: Planner mismatch yields deterministic canonical failure artifact ---


def test_open_terminal_drifting_to_write_text_fails_closed(tmp_path: Path, monkeypatch: Any):
    """Regression: 'open terminal' drifting to write_text must fail closed before side effects."""
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="files.write_text")

    from voxera.core.queue_daemon import MissionQueueDaemon

    side_effect_called = {"n": 0}

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-mismatch-open", {"goal": "open terminal"})

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _should_not_be_called(*a, **kw):
        side_effect_called["n"] += 1
        from voxera.models import RunResult

        return RunResult(ok=True, output="", data={})

    daemon.mission_runner.run = _should_not_be_called  # type: ignore[method-assign]
    daemon.process_pending_once()

    # Job must be in failed bucket
    assert (queue_root / "failed" / "job-mismatch-open.json").exists()
    assert not (queue_root / "done" / "job-mismatch-open.json").exists()
    # Side effects must NOT have fired
    assert side_effect_called["n"] == 0

    result = _read_artifact(queue_root, "job-mismatch-open", "execution_result.json")
    assert result["ok"] is False
    assert result["terminal_outcome"] == "failed"
    assert result["evaluation_reason"] == "simple_intent_skill_family_mismatch"
    assert result["stop_reason"] == "planner_intent_route_rejected"

    ir = result.get("intent_route")
    assert ir is not None
    assert ir["intent_kind"] == "open_resource"
    assert "files.write_text" not in ir["allowed_skill_ids"]

    plan = _read_artifact(queue_root, "job-mismatch-open", "plan.json")
    assert plan["planning_error"]["evaluation_reason"] == "simple_intent_skill_family_mismatch"
    assert plan["planning_error"]["planned_skill_id"] == "files.write_text"
    assert plan["intent_route"]["intent_kind"] == "open_resource"


def test_write_file_drifting_to_wrong_family_fails_closed(tmp_path: Path, monkeypatch: Any):
    """Regression: 'write file' drifting to a non-write skill must fail closed.

    We use system.status as the wrong skill (passes snapshot validation but
    is in the advisory family, not the write family).
    """
    _force_policy_ask(monkeypatch)
    # system.status passes validation but is NOT in the write-file allowed family
    _stub_plan_with_skill(monkeypatch, skill_id="system.status")

    from voxera.core.queue_daemon import MissionQueueDaemon

    side_effect_called = {"n": 0}
    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-mismatch-write", {"goal": "write text.txt with hello world"})

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _should_not_run(*a, **kw):
        side_effect_called["n"] += 1
        from voxera.models import RunResult

        return RunResult(ok=True, output="", data={})

    daemon.mission_runner.run = _should_not_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-mismatch-write.json").exists()
    assert side_effect_called["n"] == 0

    result = _read_artifact(queue_root, "job-mismatch-write", "execution_result.json")
    assert result["ok"] is False
    assert result["evaluation_reason"] == "simple_intent_skill_family_mismatch"

    ir = result.get("intent_route")
    assert ir is not None
    assert ir["intent_kind"] == "write_file"


def test_advisory_drifting_to_write_text_fails_closed(tmp_path: Path, monkeypatch: Any):
    """Regression: advisory request drifting into mutating skill must fail closed."""
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="files.write_text")

    from voxera.core.queue_daemon import MissionQueueDaemon

    side_effect_called = {"n": 0}
    queue_root = tmp_path / "queue"
    _make_inbox_job(
        queue_root, "job-mismatch-advisory", {"goal": "what is my current system status?"}
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _should_not_run(*a, **kw):
        side_effect_called["n"] += 1
        from voxera.models import RunResult

        return RunResult(ok=True, output="", data={})

    daemon.mission_runner.run = _should_not_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-mismatch-advisory.json").exists()
    assert side_effect_called["n"] == 0

    result = _read_artifact(queue_root, "job-mismatch-advisory", "execution_result.json")
    assert result["ok"] is False
    assert result["evaluation_reason"] == "simple_intent_skill_family_mismatch"

    ir = result.get("intent_route")
    assert ir is not None
    assert ir["intent_kind"] == "assistant_question"


def test_open_terminal_routes_to_terminal_run_once_succeeds(tmp_path: Path, monkeypatch: Any):
    """Regression (STV pr144-open-terminal): 'open terminal' with system.terminal_run_once
    must succeed — not be rejected as an open_resource mismatch.

    The planner may use system.terminal_run_once (the deterministic terminal demo skill)
    instead of system.open_app for 'open terminal' goals.  Both are valid.
    """
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="system.terminal_run_once")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-open-terminal-tro", {"goal": "open terminal"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="terminal opened",
        data={
            "results": [{"step": 1, "skill": "system.terminal_run_once", "args": {}, "ok": True}],
            "step_outcomes": [
                {"step": 1, "skill": "system.terminal_run_once", "outcome": "succeeded"}
            ],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    # Job must succeed — not be rejected with planner_intent_route_rejected
    assert (queue_root / "done" / "job-open-terminal-tro.json").exists(), (
        "open terminal with terminal_run_once should not be rejected as mismatch"
    )
    assert not (queue_root / "failed" / "job-open-terminal-tro.json").exists()
    result = _read_artifact(queue_root, "job-open-terminal-tro", "execution_result.json")
    assert result["ok"] is True
    # intent_route in execution_result is only written on mismatch; check envelope instead.
    envelope = _read_artifact(queue_root, "job-open-terminal-tro", "execution_envelope.json")
    si = envelope["request"].get("simple_intent")
    assert si is not None
    assert si["intent_kind"] == "open_resource"
    assert "system.terminal_run_once" in si["allowed_skill_ids"]


def test_read_file_clipboard_copy_fails_closed_regression(tmp_path: Path, monkeypatch: Any):
    """Regression (STV pr144-read): 'read file' route must reject clipboard.copy as first step.

    The planner safety rewrite converts some sandbox.exec steps to clipboard.copy for
    non-explicit goals.  When the intent is clearly read_file, clipboard.copy is outside
    the allowed family and must be blocked fail-closed before any side effects.
    """
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="clipboard.copy")

    from voxera.core.queue_daemon import MissionQueueDaemon

    side_effect_called = {"n": 0}
    queue_root = tmp_path / "queue"
    _make_inbox_job(
        queue_root,
        "job-read-clipboard-mismatch",
        {"goal": "read ~/VoxeraOS/notes/file.txt"},
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _should_not_run(*a, **kw):
        side_effect_called["n"] += 1
        from voxera.models import RunResult

        return RunResult(ok=True, output="", data={})

    daemon.mission_runner.run = _should_not_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-read-clipboard-mismatch.json").exists(), (
        "clipboard.copy must be rejected as first step for read_file intent"
    )
    assert not (queue_root / "done" / "job-read-clipboard-mismatch.json").exists()
    assert side_effect_called["n"] == 0, "mission.run must not be called on mismatch"

    result = _read_artifact(queue_root, "job-read-clipboard-mismatch", "execution_result.json")
    assert result["ok"] is False
    assert result["evaluation_reason"] == "simple_intent_skill_family_mismatch"
    assert result["stop_reason"] == "planner_intent_route_rejected"

    ir = result.get("intent_route")
    assert ir is not None
    assert ir["intent_kind"] == "read_file"
    assert "clipboard.copy" not in ir["allowed_skill_ids"]
    assert "files.read_text" in ir["allowed_skill_ids"]


def test_mismatch_blocked_before_side_effects(tmp_path: Path, monkeypatch: Any):
    """Mismatch detection must stop execution before any side effects (mission.run not called).

    We use system.status as the wrong skill: it passes snapshot validation but
    is NOT in the read_file allowed family.
    """
    _force_policy_ask(monkeypatch)
    # system.status passes validation but is NOT in the read-file allowed family
    _stub_plan_with_skill(monkeypatch, skill_id="system.status")

    from voxera.core.queue_daemon import MissionQueueDaemon

    run_called = {"n": 0}
    queue_root = tmp_path / "queue"
    # "read ~/VoxeraOS/notes/foo.txt" is read_file, but plan produces system.status
    _make_inbox_job(
        queue_root,
        "job-mismatch-side-effects",
        {"goal": "read ~/VoxeraOS/notes/foo.txt"},
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _count_run(*a, **kw):
        run_called["n"] += 1
        from voxera.models import RunResult

        return RunResult(ok=True, output="", data={})

    daemon.mission_runner.run = _count_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-mismatch-side-effects.json").exists()
    assert run_called["n"] == 0, "mission.run must not be called on mismatch"


# --- Test 7: Queue surfaces stay coherent ---


def test_intent_route_present_in_plan_artifact(tmp_path: Path, monkeypatch: Any):
    """plan.json should include intent_route metadata for goal-kind simple intents."""
    _force_policy_ask(monkeypatch)
    _stub_plan_with_skill(monkeypatch, skill_id="files.write_text")

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(queue_root, "job-plan-intent", {"goal": "write notes.txt with hello"})

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="ok",
        data={
            "results": [],
            "step_outcomes": [],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    plan = _read_artifact(queue_root, "job-plan-intent", "plan.json")
    ir = plan.get("intent_route")
    assert ir is not None
    assert ir["intent_kind"] == "write_file"
    assert ir["deterministic"] is True
    assert "files.write_text" in ir["allowed_skill_ids"]


def test_non_goal_job_has_no_simple_intent(tmp_path: Path, monkeypatch: Any):
    """mission_id and inline_steps jobs should have no simple_intent in the envelope."""
    _force_policy_ask(monkeypatch)

    from voxera.core.queue_daemon import MissionQueueDaemon
    from voxera.models import RunResult

    queue_root = tmp_path / "queue"
    _make_inbox_job(
        queue_root,
        "job-inline-no-intent",
        {
            "steps": [{"skill_id": "system.status", "args": {}}],
            "title": "Inline job",
        },
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.mission_runner.run = lambda *a, **kw: RunResult(  # type: ignore[method-assign]
        ok=True,
        output="ok",
        data={
            "results": [],
            "step_outcomes": [],
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "total_steps": 1,
        },
    )
    daemon.process_pending_once()

    envelope = _read_artifact(queue_root, "job-inline-no-intent", "execution_envelope.json")
    # simple_intent should be None for non-goal jobs
    assert envelope["request"].get("simple_intent") is None


# ---------------------------------------------------------------------------
# Intent allowed skills table sanity
# ---------------------------------------------------------------------------


def test_intent_allowed_skills_table_is_complete():
    """All known intent kinds must have an entry in INTENT_ALLOWED_SKILLS."""
    expected_kinds = {
        "assistant_question",
        "open_resource",
        "write_file",
        "read_file",
        "run_command",
        "unknown_or_ambiguous",
    }
    assert set(INTENT_ALLOWED_SKILLS.keys()) == expected_kinds


def test_unknown_or_ambiguous_has_empty_allowed_skills():
    assert len(INTENT_ALLOWED_SKILLS["unknown_or_ambiguous"]) == 0


def test_to_dict_is_serialisable():
    result = classify_simple_operator_intent(goal="open terminal")
    d = result.to_dict()
    assert isinstance(d["intent_kind"], str)
    assert isinstance(d["deterministic"], bool)
    assert isinstance(d["allowed_skill_ids"], list)
    assert isinstance(d["routing_reason"], str)
    assert isinstance(d["fail_closed"], bool)
    # Should round-trip through JSON
    _ = json.dumps(d)
