from __future__ import annotations

import asyncio
import importlib
import json
import time
from pathlib import Path
from typing import Any

from ..health import (
    compute_brain_backoff_s,
    read_health_snapshot,
    record_brain_backoff_applied,
    record_health_ok,
    record_mission_success,
)
from ..operator_assistant import ASSISTANT_JOB_KIND
from .capabilities_snapshot import (
    generate_capabilities_snapshot,
    validate_mission_id_against_snapshot,
    validate_mission_steps_against_snapshot,
)
from .execution_evaluator import evaluate_run_result, replan_allowed_for_class
from .mission_planner import MissionPlannerError
from .missions import MissionStep, MissionTemplate, get_mission
from .queue_contracts import build_execution_envelope, detect_request_kind
from .queue_job_intent import build_queue_job_intent
from .simple_intent import (
    SimpleIntentResult,
    check_skill_family_mismatch,
    classify_simple_operator_intent,
    sanitize_serialized_intent_route,
)


def _queue_daemon_module() -> Any:
    return importlib.import_module("voxera.core.queue_daemon")


class QueueExecutionMixin:
    current_job_ref: str | None

    def _normalize_payload(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
        mission_id = payload.get("mission_id", payload.get("mission"))
        goal = payload.get("goal") if "goal" in payload else payload.get("plan_goal")
        normalized: dict[str, Any] = {}
        if mission_id is not None:
            normalized["mission_id"] = str(mission_id)
        if goal is not None:
            normalized["goal"] = str(goal)

        title = payload.get("title")
        if title is not None:
            normalized["title"] = str(title)

        steps = payload.get("steps")
        if steps is not None:
            normalized["steps"] = steps

        if "approval_required" in payload:
            normalized["approval_required"] = payload.get("approval_required") is True

        if isinstance(payload.get("_simple_intent"), dict):
            normalized["_simple_intent"] = sanitize_serialized_intent_route(
                payload.get("_simple_intent")
            )

        normalized["job_intent"] = build_queue_job_intent(payload, source_lane="queue_daemon")
        return normalized

    def _build_inline_mission(
        self: Any, payload: dict[str, Any], *, job_ref: str
    ) -> MissionTemplate:
        steps_raw = payload.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError("job steps must be a non-empty list")

        mission_steps: list[MissionStep] = []
        for idx, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"job step {idx} must be an object")

            skill_id_raw = item.get("skill_id", item.get("skill"))
            skill_id = str(skill_id_raw or "").strip()
            if not skill_id:
                raise ValueError(
                    f"job step {idx} missing skill_id (or legacy skill) for {Path(job_ref).name}"
                )

            args_raw = item.get("args", {})
            if args_raw is None:
                args_raw = {}
            if not isinstance(args_raw, dict):
                raise ValueError(f"job step {idx} args must be an object")

            mission_steps.append(MissionStep(skill_id=skill_id, args=dict(args_raw)))

        mission_id = Path(job_ref).stem
        title = str(payload.get("title") or f"Queued Mission {mission_id}")
        goal = str(payload.get("goal") or "User-defined queued mission")
        return MissionTemplate(
            id=mission_id,
            title=title,
            goal=goal,
            notes="inline_queue_job",
            steps=mission_steps,
        )

    def _apply_brain_backoff_before_plan_attempt(self: Any) -> None:
        """Sleep once before a planning attempt when failure backoff is active."""
        snapshot = read_health_snapshot(self.queue_root)
        wait_s = compute_brain_backoff_s(snapshot.get("consecutive_brain_failures", 0))
        if wait_s <= 0:
            return
        _queue_daemon_module().time.sleep(wait_s)
        record_brain_backoff_applied(
            self.queue_root,
            wait_s=wait_s,
            now_ts=time.time(),
        )

    def _build_mission_for_payload(
        self: Any, payload: dict[str, Any], *, job_ref: str
    ) -> MissionTemplate:
        normalized = self._normalize_payload(payload)
        snapshot = generate_capabilities_snapshot(self.mission_runner.skill_runner.registry)
        if "mission_id" in normalized:
            validate_mission_id_against_snapshot(normalized["mission_id"], snapshot)
            mission = get_mission(normalized["mission_id"])
            validate_mission_steps_against_snapshot(mission, snapshot)
            return mission
        if "steps" in normalized:
            mission = self._build_inline_mission(normalized, job_ref=job_ref)
            validate_mission_steps_against_snapshot(mission, snapshot)
            return mission
        if "goal" in normalized:
            try:
                self._apply_brain_backoff_before_plan_attempt()
                mission = asyncio.run(
                    _queue_daemon_module().plan_mission(
                        goal=normalized["goal"],
                        cfg=self.cfg,
                        registry=self.mission_runner.skill_runner.registry,
                        source="queue",
                        job_ref=job_ref,
                        queue_root=self.queue_root,
                    )
                )
                validate_mission_steps_against_snapshot(mission, snapshot)
                return mission
            except MissionPlannerError as exc:
                raise RuntimeError(str(exc)) from exc
        raise ValueError(
            "job must contain mission_id (or mission), goal (or plan_goal), or inline steps"
        )

    def _is_ready_job_file(self: Any, path: Path) -> bool:
        if path.parent != self.inbox or not path.is_file():
            return False
        if self._is_snapshot_artifact(path):
            return False
        name = path.name
        if not name.endswith(".json"):
            return False
        if name.startswith("."):
            return False
        blocked_suffixes = (
            ".pending.json",
            ".approval.json",
            ".state.json",
            ".tmp.json",
            ".partial.json",
        )
        return not name.endswith(blocked_suffixes)

    def _load_job_payload_with_retry(self: Any, job_path: Path) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, _queue_daemon_module()._PARSE_RETRY_ATTEMPTS + 1):
            try:
                payload = json.loads(job_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("job payload must be a JSON object")
                if attempt > 1:
                    _queue_daemon_module().log(
                        {
                            "event": "queue_job_parse_stabilized",
                            "attempt": attempt,
                            "path": str(job_path),
                        }
                    )
                return payload
            except json.JSONDecodeError as exc:
                last_error = exc
                if attempt >= _queue_daemon_module()._PARSE_RETRY_ATTEMPTS:
                    break
                _queue_daemon_module().log(
                    {"event": "queue_job_retry_parse", "attempt": attempt, "path": str(job_path)}
                )
                _queue_daemon_module().time.sleep(_queue_daemon_module()._PARSE_RETRY_BACKOFF_S)

        if last_error is not None:
            raise last_error
        raise ValueError("job payload must be a JSON object")

    def _is_job_state_sidecar(self: Any, path: Path) -> bool:
        return path.name.endswith(".state.json")

    def _is_metadata_sidecar(self: Any, path: Path) -> bool:
        return path.name.endswith(
            (
                ".pending.json",
                ".approval.json",
                ".error.json",
                ".state.json",
                ".tmp.json",
                ".partial.json",
            )
        )

    def _is_primary_job_json(self: Any, path: Path) -> bool:
        return (
            path.name.endswith(".json")
            and path.name != "health.json"
            and not self._is_snapshot_artifact(path)
            and not self._is_metadata_sidecar(path)
        )

    def _primary_jobs_in_bucket(self: Any, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return sorted(
            p for p in directory.glob("*.json") if p.is_file() and self._is_primary_job_json(p)
        )

    def _pending_primary_jobs(self: Any) -> list[Path]:
        return self._primary_jobs_in_bucket(self.pending)

    def _request_kind(self: Any, payload: dict[str, Any]) -> str:
        return detect_request_kind(payload)

    def _is_assistant_request(self: Any, payload: dict[str, Any]) -> bool:
        return self._request_kind(payload) == ASSISTANT_JOB_KIND

    def _max_replan_attempts(self: Any) -> int:
        try:
            return max(0, int(getattr(self.cfg, "max_replan_attempts", 1)))
        except Exception:
            return 1

    def _plan_delta(
        self: Any, previous: MissionTemplate, current: MissionTemplate
    ) -> dict[str, Any]:
        previous_steps = [{"skill_id": s.skill_id, "args": s.args} for s in previous.steps]
        current_steps = [{"skill_id": s.skill_id, "args": s.args} for s in current.steps]
        return {
            "mission_id_changed": previous.id != current.id,
            "total_steps_before": len(previous.steps),
            "total_steps_after": len(current.steps),
            "steps_changed": previous_steps != current_steps,
        }

    def _planning_error_metadata(self: Any, exc: Exception) -> tuple[str, str, bool]:
        message = str(exc).strip()
        lowered = message.lower()
        if "planner referenced unknown skill" in lowered or "unknown skill" in lowered:
            return ("replannable_mismatch", "skill_not_found", True)
        if "without a valid skill_id" in lowered:
            return ("replannable_mismatch", "planner_skill_mismatch", True)
        if "args" in lowered and "invalid" in lowered:
            return ("replannable_mismatch", "arg_shape_mismatch", True)
        return ("terminal_failure", "planning_failure", False)

    def _classify_goal_intent(self: Any, payload: dict[str, Any]) -> SimpleIntentResult:
        """Return the simple intent classification for a goal-kind payload."""
        goal = payload.get("goal") or payload.get("plan_goal")
        goal_text = str(goal).strip() if goal else None
        action_hints_raw = payload.get("action_hints")
        action_hints = (
            [str(h) for h in action_hints_raw if str(h).strip()]
            if isinstance(action_hints_raw, list)
            else None
        )
        return classify_simple_operator_intent(goal=goal_text, action_hints=action_hints)

    def _write_simple_intent_mismatch_artifact(
        self: Any,
        *,
        job_ref: str,
        payload: dict[str, Any],
        intent: SimpleIntentResult,
        first_step_skill_id: str,
        attempt_index: int,
        replan_count: int,
        max_replans: int,
    ) -> None:
        artifact_dir = self._job_artifacts_dir(job_ref)
        intent_route = intent.to_dict()
        mismatch_plan = {
            "job": Path(job_ref).name,
            "attempt_index": attempt_index,
            "replan_count": replan_count,
            "max_replans": max_replans,
            "supersedes_attempt": attempt_index - 1 if attempt_index > 1 else None,
            "plan_delta": None,
            "payload": payload,
            "mission": None,
            "intent_route": intent_route,
            "planning_error": {
                "evaluation_class": "terminal_failure",
                "evaluation_reason": "simple_intent_skill_family_mismatch",
                "error": (
                    f"simple intent '{intent.intent_kind}' allows skills "
                    f"{sorted(intent.allowed_skill_ids)} but planner produced "
                    f"first step '{first_step_skill_id}'"
                ),
                "intent_kind": intent.intent_kind,
                "allowed_skill_ids": sorted(intent.allowed_skill_ids),
                "planned_skill_id": first_step_skill_id,
                "routing_reason": intent.routing_reason,
            },
        }
        (artifact_dir / "plan.json").write_text(
            json.dumps(mismatch_plan, indent=2), encoding="utf-8"
        )
        (artifact_dir / f"plan.attempt-{attempt_index}.json").write_text(
            json.dumps(mismatch_plan, indent=2), encoding="utf-8"
        )

    def _write_planning_failure_attempt_artifact(
        self: Any,
        *,
        job_ref: str,
        payload: dict[str, Any],
        attempt_index: int,
        replan_count: int,
        max_replans: int,
        evaluation_class: str,
        evaluation_reason: str,
        error_text: str,
    ) -> None:
        artifact_dir = self._job_artifacts_dir(job_ref)
        plan_payload = {
            "job": Path(job_ref).name,
            "attempt_index": attempt_index,
            "replan_count": replan_count,
            "max_replans": max_replans,
            "supersedes_attempt": attempt_index - 1 if attempt_index > 1 else None,
            "plan_delta": None,
            "payload": payload,
            "mission": None,
            "planning_error": {
                "evaluation_class": evaluation_class,
                "evaluation_reason": evaluation_reason,
                "error": error_text,
            },
        }
        (artifact_dir / "plan.json").write_text(
            json.dumps(plan_payload, indent=2), encoding="utf-8"
        )
        (artifact_dir / f"plan.attempt-{attempt_index}.json").write_text(
            json.dumps(plan_payload, indent=2), encoding="utf-8"
        )

    def process_job_file(self: Any, job_path: Path) -> bool:
        self.ensure_dirs()
        if not job_path.exists():
            return False
        if not self._is_primary_job_json(job_path):
            _queue_daemon_module().log(
                {
                    "event": "queue_metadata_ignored",
                    "path": str(job_path),
                    "reason": "not_primary_job_json",
                }
            )
            return False

        self.current_job_ref = str(job_path)
        try:
            _queue_daemon_module().log({"event": "queue_job_received", "job": str(job_path)})
            self._update_job_state(str(job_path), lifecycle_state="queued")
            self._update_job_state(str(job_path), lifecycle_state="planning")
            if self._shutdown_requested:
                _queue_daemon_module().log(
                    {"event": "queue_job_skipped_shutdown", "job": str(job_path)}
                )
                return False

            try:
                payload = self._load_job_payload_with_retry(job_path)
                if isinstance(payload.get("_simple_intent"), dict):
                    payload["_simple_intent"] = sanitize_serialized_intent_route(
                        payload["_simple_intent"]
                    )
                if self._is_assistant_request(payload):
                    request_kind = self._request_kind(payload)
                    fast_lane_eligible, fast_lane_reason = (
                        _queue_daemon_module().queue_assistant.evaluate_assistant_fast_lane_eligibility(
                            payload,
                            request_kind=request_kind,
                        )
                    )
                    lane = "fast_read_only" if fast_lane_eligible else "queue"
                    fast_lane_meta = {
                        "used": fast_lane_eligible,
                        "eligible": fast_lane_eligible,
                        "eligibility_reason": fast_lane_reason,
                        "request_kind": request_kind,
                    }
                    self._write_action_event(
                        str(job_path),
                        "assistant_fast_lane_evaluated",
                        request_kind=request_kind,
                        fast_lane_eligible=fast_lane_eligible,
                        fast_lane_reason=fast_lane_reason,
                        execution_lane=lane,
                    )
                    _queue_daemon_module().log(
                        {
                            "event": "assistant_fast_lane_evaluated",
                            "job": str(job_path),
                            "request_kind": request_kind,
                            "fast_lane_eligible": fast_lane_eligible,
                            "fast_lane_reason": fast_lane_reason,
                            "execution_lane": lane,
                        }
                    )
                    return self._process_assistant_job(
                        job_path,
                        payload,
                        execution_lane=lane,
                        fast_lane=fast_lane_meta,
                    )
                payload = self._normalize_payload(payload)
                if self._ensure_hard_approval_gate(job_path, payload=payload):
                    return False
            except Exception as exc:
                _queue_daemon_module().log(
                    {
                        "event": "queue_job_invalid",
                        "job": str(job_path),
                        "filename": job_path.name,
                        "reason": repr(exc),
                    }
                )
                moved = self._move_job(job_path, self.failed)
                if moved is None:
                    return False
                sidecar_payload = (
                    payload if "payload" in locals() and isinstance(payload, dict) else None
                )
                self._write_failed_error_sidecar(moved, error=repr(exc), payload=sidecar_payload)
                self._write_execution_result_artifacts(
                    str(moved),
                    rr_data={
                        "results": [],
                        "step_outcomes": [],
                        "total_steps": 0,
                        "lifecycle_state": "step_failed",
                        "attempt_index": 1,
                        "replan_count": 0,
                        "max_replans": self._max_replan_attempts(),
                        "evaluation_class": "terminal_failure",
                        "evaluation_reason": "job_invalid",
                        "stop_reason": "job_invalid",
                    },
                    ok=False,
                    terminal_outcome="failed",
                    error=repr(exc),
                )
                self._update_job_state(
                    str(moved),
                    lifecycle_state="step_failed",
                    payload=sidecar_payload if isinstance(sidecar_payload, dict) else None,
                    terminal_outcome="failed",
                    failure_summary=repr(exc),
                )
                self.stats.failed += 1
                _queue_daemon_module().log(
                    {"event": "queue_job_failed", "job": str(moved), "error": repr(exc)}
                )
                self.prune_failed_artifacts()
                return False

            kind = self._request_kind(payload)
            max_replans = self._max_replan_attempts()
            attempt_index = 0
            replan_count = 0
            previous_mission: MissionTemplate | None = None

            # Classify simple intent for goal-kind requests so we can constrain
            # and validate the planner's first-step skill choice.
            simple_intent: SimpleIntentResult | None = None
            if kind == "goal":
                simple_intent = self._classify_goal_intent(payload)
                # Stash on payload for envelope/artifact propagation.
                payload["_simple_intent"] = sanitize_serialized_intent_route(
                    simple_intent.to_dict()
                )
                self._write_action_event(
                    str(job_path),
                    "queue_simple_intent_routed",
                    intent_kind=simple_intent.intent_kind,
                    deterministic=simple_intent.deterministic,
                    routing_reason=simple_intent.routing_reason,
                    allowed_skill_ids=sorted(simple_intent.allowed_skill_ids),
                    fail_closed=simple_intent.fail_closed,
                )
                _queue_daemon_module().log(
                    {
                        "event": "queue_simple_intent_routed",
                        "job": str(job_path),
                        "intent_kind": simple_intent.intent_kind,
                        "deterministic": simple_intent.deterministic,
                        "routing_reason": simple_intent.routing_reason,
                        "allowed_skill_ids": sorted(simple_intent.allowed_skill_ids),
                        "fail_closed": simple_intent.fail_closed,
                    }
                )

            while True:
                attempt_index += 1
                try:
                    mission = self._build_mission_for_payload(payload, job_ref=str(job_path))
                except Exception as exc:
                    evaluation_class, evaluation_reason, replannable = (
                        self._planning_error_metadata(exc)
                    )
                    error_text = repr(exc)
                    self._write_planning_failure_attempt_artifact(
                        job_ref=str(job_path),
                        payload=payload,
                        attempt_index=attempt_index,
                        replan_count=replan_count,
                        max_replans=max_replans,
                        evaluation_class=evaluation_class,
                        evaluation_reason=evaluation_reason,
                        error_text=error_text,
                    )
                    should_replan = (
                        replannable
                        and kind == "goal"
                        and replan_count < max_replans
                        and evaluation_class in {"retryable_failure", "replannable_mismatch"}
                    )
                    if should_replan:
                        replan_count += 1
                        self._write_action_event(
                            str(job_path),
                            "queue_job_replanned",
                            attempt_index=attempt_index,
                            evaluation_class=evaluation_class,
                            evaluation_reason=evaluation_reason,
                            replan_count=replan_count,
                            max_replans=max_replans,
                        )
                        _queue_daemon_module().log(
                            {
                                "event": "queue_job_replanned",
                                "job": str(job_path),
                                "attempt_index": attempt_index,
                                "evaluation_class": evaluation_class,
                                "evaluation_reason": evaluation_reason,
                                "replan_count": replan_count,
                                "max_replans": max_replans,
                            }
                        )
                        continue

                    _queue_daemon_module().log(
                        {
                            "event": "queue_job_invalid",
                            "job": str(job_path),
                            "filename": job_path.name,
                            "reason": error_text,
                            "attempt_index": attempt_index,
                        }
                    )
                    moved = self._move_job(job_path, self.failed)
                    if moved is None:
                        return False
                    self._write_failed_error_sidecar(moved, error=error_text, payload=payload)
                    rr_data = {
                        "results": [],
                        "step_outcomes": [],
                        "total_steps": 0,
                        "lifecycle_state": "step_failed",
                        "attempt_index": attempt_index,
                        "replan_count": replan_count,
                        "max_replans": max_replans,
                        "evaluation_class": evaluation_class,
                        "evaluation_reason": evaluation_reason,
                        "stop_reason": "planning_failure",
                    }
                    self._write_execution_result_artifacts(
                        str(moved),
                        rr_data=rr_data,
                        ok=False,
                        terminal_outcome="failed",
                        error=error_text,
                    )
                    self._update_job_state(
                        str(moved),
                        lifecycle_state="step_failed",
                        payload=payload,
                        mission=previous_mission,
                        rr_data=rr_data,
                        terminal_outcome="failed",
                        failure_summary=error_text,
                    )
                    self.stats.failed += 1
                    _queue_daemon_module().log(
                        {"event": "queue_job_failed", "job": str(moved), "error": error_text}
                    )
                    self.prune_failed_artifacts()
                    return False

                # --- Simple intent mismatch detection --------------------------------
                # If we recognised a deterministic simple intent, validate that the
                # plan's first step belongs to the allowed skill family.  If not,
                # fail closed before any side effects.
                if (
                    simple_intent is not None
                    and simple_intent.deterministic
                    and simple_intent.fail_closed
                    and mission.steps
                ):
                    first_skill = mission.steps[0].skill_id
                    mismatch, mismatch_reason = check_skill_family_mismatch(
                        simple_intent, first_skill
                    )
                    if mismatch:
                        mismatch_error = (
                            f"planner_intent_route_rejected: simple intent "
                            f"'{simple_intent.intent_kind}' (allowed: "
                            f"{sorted(simple_intent.allowed_skill_ids)}) but "
                            f"planner produced first step '{first_skill}'"
                        )
                        intent_route_dict = sanitize_serialized_intent_route(
                            simple_intent.to_dict()
                        )
                        self._write_simple_intent_mismatch_artifact(
                            job_ref=str(job_path),
                            payload=payload,
                            intent=simple_intent,
                            first_step_skill_id=first_skill,
                            attempt_index=attempt_index,
                            replan_count=replan_count,
                            max_replans=max_replans,
                        )
                        self._write_action_event(
                            str(job_path),
                            "queue_simple_intent_mismatch",
                            intent_kind=simple_intent.intent_kind,
                            allowed_skill_ids=sorted(simple_intent.allowed_skill_ids),
                            planned_skill_id=first_skill,
                            mismatch_reason=mismatch_reason,
                            routing_reason=simple_intent.routing_reason,
                        )
                        _queue_daemon_module().log(
                            {
                                "event": "queue_simple_intent_mismatch",
                                "job": str(job_path),
                                "intent_kind": simple_intent.intent_kind,
                                "allowed_skill_ids": sorted(simple_intent.allowed_skill_ids),
                                "planned_skill_id": first_skill,
                                "mismatch_reason": mismatch_reason,
                                "routing_reason": simple_intent.routing_reason,
                            }
                        )
                        moved = self._move_job(job_path, self.failed)
                        if moved is None:
                            return False
                        self._write_failed_error_sidecar(
                            moved, error=mismatch_error, payload=payload
                        )
                        mismatch_rr_data = {
                            "results": [],
                            "step_outcomes": [],
                            "total_steps": len(mission.steps),
                            "lifecycle_state": "step_failed",
                            "attempt_index": attempt_index,
                            "replan_count": replan_count,
                            "max_replans": max_replans,
                            "evaluation_class": "terminal_failure",
                            "evaluation_reason": "simple_intent_skill_family_mismatch",
                            "stop_reason": "planner_intent_route_rejected",
                            "intent_route": intent_route_dict,
                        }
                        self._write_execution_result_artifacts(
                            str(moved),
                            rr_data=mismatch_rr_data,
                            ok=False,
                            terminal_outcome="failed",
                            error=mismatch_error,
                        )
                        self._update_job_state(
                            str(moved),
                            lifecycle_state="step_failed",
                            payload=payload,
                            mission=previous_mission,
                            rr_data=mismatch_rr_data,
                            terminal_outcome="failed",
                            failure_summary=mismatch_error,
                        )
                        self.stats.failed += 1
                        _queue_daemon_module().log(
                            {
                                "event": "queue_job_failed",
                                "job": str(moved),
                                "error": mismatch_error,
                            }
                        )
                        self.prune_failed_artifacts()
                        return False
                # --- End simple intent mismatch detection ----------------------------

                plan_delta = (
                    self._plan_delta(previous_mission, mission)
                    if previous_mission is not None
                    else {
                        "mission_id_changed": False,
                        "total_steps_before": len(mission.steps),
                        "total_steps_after": len(mission.steps),
                        "steps_changed": False,
                    }
                )
                self._write_plan_artifact(
                    str(job_path),
                    payload=payload,
                    mission=mission,
                    attempt_index=attempt_index,
                    replan_count=replan_count,
                    max_replans=max_replans,
                    supersedes_attempt=attempt_index - 1 if attempt_index > 1 else None,
                    plan_delta=plan_delta,
                )
                envelope = build_execution_envelope(
                    job_ref=str(job_path),
                    payload=payload,
                    mission=mission,
                    queue_root=self.queue_root,
                    artifact_root=self.artifacts,
                    normalized_mode="mission",
                    execution_lane="queue",
                    fast_lane=None,
                    attempt_index=attempt_index,
                    replan_count=replan_count,
                    max_replans=max_replans,
                    supersedes_attempt=attempt_index - 1 if attempt_index > 1 else None,
                )
                (self._job_artifacts_dir(str(job_path)) / "execution_envelope.json").write_text(
                    json.dumps(envelope, indent=2), encoding="utf-8"
                )
                self._update_job_state(
                    str(job_path),
                    lifecycle_state="running",
                    payload=payload,
                    mission=mission,
                    rr_data={"total_steps": len(mission.steps)},
                )

                _queue_daemon_module().log(
                    {
                        "event": "queue_job_started",
                        "kind": kind,
                        "mission": payload.get("mission_id"),
                        "goal": payload.get("goal"),
                        "attempt_index": attempt_index,
                        "replan_count": replan_count,
                    }
                )
                rr = self.mission_runner.run(mission, context={"queue_job": str(job_path)})
                if self._shutdown_requested:
                    self._finalize_job_shutdown_failure(
                        job_path, signal_reason=self._shutdown_reason, payload=payload
                    )
                    return False

                evaluation = evaluate_run_result(run_result=rr, request_kind=kind)
                rr.data.setdefault("attempt_index", attempt_index)
                rr.data.setdefault("replan_count", replan_count)
                rr.data.setdefault("max_replans", max_replans)
                rr.data.setdefault("evaluation_class", evaluation.evaluation_class)
                rr.data.setdefault("evaluation_reason", evaluation.reason)
                # Propagate simple_intent into intent_route so execution_result.json
                # carries the same metadata as execution_envelope.json and plan.json
                # for ALL goal-kind jobs, not just mismatch failures.
                if simple_intent is not None:
                    rr.data.setdefault(
                        "intent_route", sanitize_serialized_intent_route(simple_intent.to_dict())
                    )

                should_replan = (
                    evaluation.replan_allowed
                    and replan_allowed_for_class(evaluation.evaluation_class)
                    and replan_count < max_replans
                )

                if should_replan:
                    replan_count += 1
                    rr.data["stop_reason"] = "replanned"
                    self._write_action_event(
                        str(job_path),
                        "queue_job_replanned",
                        attempt_index=attempt_index,
                        evaluation_class=evaluation.evaluation_class,
                        evaluation_reason=evaluation.reason,
                        replan_count=replan_count,
                        max_replans=max_replans,
                    )
                    _queue_daemon_module().log(
                        {
                            "event": "queue_job_replanned",
                            "job": str(job_path),
                            "attempt_index": attempt_index,
                            "evaluation_class": evaluation.evaluation_class,
                            "evaluation_reason": evaluation.reason,
                            "replan_count": replan_count,
                            "max_replans": max_replans,
                        }
                    )
                    previous_mission = mission
                    continue

                if rr.data.get("status") == "pending_approval":
                    rr.data.setdefault("stop_reason", "awaiting_approval")
                    moved = self._move_job(job_path, self.pending)
                    if moved is None:
                        return False
                    self._write_pending_artifacts(
                        moved, payload=payload, mission=mission, run_data=rr.data
                    )
                    self._write_execution_result_artifacts(
                        str(moved),
                        rr_data=rr.data,
                        ok=False,
                        terminal_outcome="awaiting_approval",
                        error=rr.error,
                    )
                    self._update_job_state(
                        str(moved),
                        lifecycle_state="awaiting_approval",
                        payload=payload,
                        mission=mission,
                        rr_data=rr.data,
                        approval_status="pending",
                    )
                    self._write_action_event(
                        str(moved), "queue_job_pending_approval", step=rr.data.get("step")
                    )
                    _queue_daemon_module().log(
                        {
                            "event": "queue_job_pending_approval",
                            "job": str(moved),
                            "step": rr.data.get("step"),
                            "reason": rr.data.get("reason"),
                        }
                    )
                    return False

                if not rr.ok:
                    rr.data.setdefault("stop_reason", "terminal_failure")
                    moved = self._move_job(job_path, self.failed)
                    if moved is None:
                        return False
                    error_text = rr.error or "mission failed"
                    self._write_failed_error_sidecar(moved, error=error_text, payload=payload)
                    self._write_execution_result_artifacts(
                        str(moved),
                        rr_data=rr.data,
                        ok=False,
                        terminal_outcome=str(rr.data.get("terminal_outcome") or "failed"),
                        error=error_text,
                    )
                    self._update_job_state(
                        str(moved),
                        lifecycle_state=str(rr.data.get("lifecycle_state") or "step_failed"),
                        payload=payload,
                        mission=mission,
                        rr_data=rr.data,
                        terminal_outcome=str(rr.data.get("terminal_outcome") or "failed"),
                        failure_summary=error_text,
                        blocked_reason=error_text
                        if str(rr.data.get("terminal_outcome") or "") == "blocked"
                        else None,
                    )
                    self.stats.failed += 1
                    _queue_daemon_module().log(
                        {"event": "queue_job_failed", "job": str(moved), "error": error_text}
                    )
                    self._write_action_event(str(moved), "queue_job_failed", error=error_text)
                    self.prune_failed_artifacts()
                    return False

                rr.data.setdefault("stop_reason", "succeeded")
                moved = self._move_job(job_path, self.done)
                if moved is None:
                    return False
                self.stats.processed += 1
                self._write_run_streams(str(moved), rr.data)
                self._update_job_state(
                    str(moved),
                    lifecycle_state="done",
                    payload=payload,
                    mission=mission,
                    rr_data=rr.data,
                    terminal_outcome=str(rr.data.get("terminal_outcome") or "succeeded"),
                    approval_status="approved" if rr.data.get("step_outcomes") else None,
                )
                self._write_action_event(str(moved), "queue_job_done")
                _queue_daemon_module().log({"event": "queue_job_done", "job": str(moved)})
                record_mission_success(self.queue_root)
                return True
        finally:
            self.current_job_ref = None

    def process_pending_once(self: Any) -> int:
        self.ensure_dirs()
        self._update_daemon_health_state(last_tick_ts_ms=int(time.time() * 1000))
        record_health_ok(self.queue_root, "daemon_tick")
        self._auto_relocate_legacy_jobs()
        self._auto_relocate_misplaced_pending_jobs()
        if self._shutdown_requested:
            _queue_daemon_module().log(
                {"event": "queue_daemon_intake_stopped", "reason": self._shutdown_reason}
            )
            return 0
        if self.is_paused():
            _queue_daemon_module().log({"event": "queue_tick_paused", "queue": str(self.inbox)})
            return 0
        processed = 0
        for job in sorted(self.inbox.glob("*.json")):
            if self._shutdown_requested:
                _queue_daemon_module().log(
                    {"event": "queue_daemon_intake_stopped", "reason": self._shutdown_reason}
                )
                break
            if not self._is_ready_job_file(job):
                continue
            self.process_job_file(job)
            processed += 1
        return processed
