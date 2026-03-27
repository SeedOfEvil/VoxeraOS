from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..audit import log
from ..models import PlanSimulation, PlanStep, RunResult
from ..skills.registry import SkillRegistry
from ..skills.result_contract import extract_skill_result
from .capability_semantics import manifest_capability_semantics


@dataclass(frozen=True)
class MissionStep:
    skill_id: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionTemplate:
    id: str
    title: str
    goal: str
    steps: list[MissionStep]
    notes: str | None = None


MISSION_TEMPLATES: dict[str, MissionTemplate] = {
    "work_mode": MissionTemplate(
        id="work_mode",
        title="Start Work Mode",
        goal="Prepare a focused workstation baseline",
        steps=[
            MissionStep(skill_id="system.open_app", args={"name": "Firefox"}),
            MissionStep(skill_id="system.open_app", args={"name": "terminal"}),
            MissionStep(skill_id="system.set_volume", args={"percent": "35"}),
        ],
        notes="Open core work apps and set baseline volume.",
    ),
    "focus_mode": MissionTemplate(
        id="focus_mode",
        title="Focus Mode",
        goal="Reduce distractions and keep only essentials active",
        steps=[
            MissionStep(skill_id="system.set_volume", args={"percent": "20"}),
            MissionStep(skill_id="system.open_app", args={"name": "firefox"}),
            MissionStep(skill_id="system.window_list"),
        ],
        notes="Lower volume, keep browser active, and verify open windows.",
    ),
    "daily_checkin": MissionTemplate(
        id="daily_checkin",
        title="Daily Check-in",
        goal="Open status surfaces and prefill notes for a daily update",
        steps=[
            MissionStep(skill_id="system.status"),
            MissionStep(skill_id="system.open_url", args={"url": "https://calendar.google.com"}),
            MissionStep(
                skill_id="files.write_text",
                args={
                    "path": "~/VoxeraOS/notes/daily-checkin.txt",
                    "text": "Today:\n- Priorities\n- Blockers\n",
                },
            ),
            MissionStep(skill_id="system.open_app", args={"name": "terminal"}),
        ],
        notes="Creates a daily check-in note and opens calendar.",
    ),
    "incident_mode": MissionTemplate(
        id="incident_mode",
        title="Incident Mode",
        goal="Bring up troubleshooting tools quickly",
        steps=[
            MissionStep(skill_id="system.open_url", args={"url": "https://status.example.com"}),
            MissionStep(skill_id="system.open_app", args={"name": "terminal"}),
            MissionStep(skill_id="system.set_volume", args={"percent": "80"}),
            MissionStep(skill_id="system.window_list"),
        ],
        notes="Open dashboard and terminal, increase alert audibility.",
    ),
    "wrap_up": MissionTemplate(
        id="wrap_up",
        title="Wrap Up",
        goal="Capture end-of-day notes and lower noise",
        steps=[
            MissionStep(
                skill_id="files.read_text", args={"path": "~/VoxeraOS/notes/daily-checkin.txt"}
            ),
            MissionStep(
                skill_id="clipboard.copy",
                args={"text": "Workday summary captured in ~/VoxeraOS/notes/daily-checkin.txt"},
            ),
            MissionStep(skill_id="system.set_volume", args={"percent": "15"}),
        ],
        notes="Review notes, copy summary text, and lower volume.",
    ),
    "notes_archive_flow": MissionTemplate(
        id="notes_archive_flow",
        title="Notes Archive Flow",
        goal="Archive an inbox note into organized notes archive with bounded file skills",
        steps=[
            MissionStep(skill_id="files.exists", args={"path": "~/VoxeraOS/notes/inbox/today.md"}),
            MissionStep(skill_id="files.stat", args={"path": "~/VoxeraOS/notes/inbox/today.md"}),
            MissionStep(
                skill_id="files.mkdir", args={"path": "~/VoxeraOS/notes/archive", "parents": True}
            ),
            MissionStep(
                skill_id="files.copy_file",
                args={
                    "source_path": "~/VoxeraOS/notes/inbox/today.md",
                    "destination_path": "~/VoxeraOS/notes/archive/today.md",
                    "overwrite": False,
                },
            ),
            MissionStep(
                skill_id="files.delete_file", args={"path": "~/VoxeraOS/notes/inbox/today.md"}
            ),
        ],
        notes=(
            "Uses bounded notes-scope file skills end-to-end: preflight existence and metadata, "
            "ensure destination directory, copy into archive, then delete original (delete requires approval policy)."
        ),
    ),
    "system_check": MissionTemplate(
        id="system_check",
        title="System Check",
        goal="Collect baseline status and verify command path",
        steps=[
            MissionStep(skill_id="system.status"),
            MissionStep(
                skill_id="files.write_text",
                args={
                    "path": "~/VoxeraOS/notes/system-check-report.txt",
                    "text": "Voxera system_check completed successfully.\n",
                },
            ),
        ],
        notes="Low-risk health check mission.",
    ),
    "system_inspect": MissionTemplate(
        id="system_inspect",
        title="System Inspection",
        goal="Collect a bounded read-only snapshot of local workstation state for audit evidence",
        steps=[
            MissionStep(skill_id="system.status"),
            MissionStep(skill_id="system.disk_usage"),
            MissionStep(skill_id="system.process_list"),
            MissionStep(skill_id="system.window_list"),
        ],
        notes=(
            "Read-only local health inspection workflow. Composes system status, disk usage, "
            "process listing, and open windows into a single bounded diagnostic snapshot. "
            "All skills are state.read / window.read only — no mutations, no network, no approval required. "
            "Executes through the queue for canonical audit trail and evidence production."
        ),
    ),
    "system_diagnostics": MissionTemplate(
        id="system_diagnostics",
        title="System Diagnostics",
        goal="Collect bounded read-only host diagnostics for queue-backed triage evidence",
        steps=[
            MissionStep(skill_id="system.host_info"),
            MissionStep(skill_id="system.memory_usage"),
            MissionStep(skill_id="system.load_snapshot"),
            MissionStep(skill_id="system.disk_usage"),
            MissionStep(skill_id="system.process_list"),
        ],
        notes=(
            "First bounded diagnostics pack for operator triage. Read-only only: host info, memory, "
            "CPU/load, disk usage, and process snapshot. Designed for deterministic queue execution "
            "with canonical artifacts/evidence (execution_result + step_results). Service-specific "
            "inspection is supported through system.service_status and system.recent_service_logs "
            "skills using explicit args and bounded limits."
        ),
    ),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mission_search_dirs() -> list[Path]:
    return [
        _repo_root() / "missions",
        Path.home() / ".config" / "voxera" / "missions",
    ]


@lru_cache(maxsize=1)
def _known_skill_ids() -> set[str]:
    reg = SkillRegistry()
    manifests = reg.discover()
    return set(manifests.keys())


def _parse_mission_file(path: Path, mission_id_hint: str) -> MissionTemplate:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"Invalid mission file {path}: unable to read ({exc})") from exc

    try:
        payload = json.loads(raw_text) if path.suffix == ".json" else yaml.safe_load(raw_text)
    except Exception as exc:
        raise ValueError(f"Invalid mission file {path}: parse error ({exc})") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Invalid mission file {path}: root must be an object")

    mission_id = payload.get("id", mission_id_hint)
    if not isinstance(mission_id, str) or not mission_id.strip():
        raise ValueError(f"Invalid mission file {path}: id must be a non-empty string")

    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"Invalid mission file {path}: steps must be a non-empty list")

    known_skills = _known_skill_ids()
    steps: list[MissionStep] = []
    for idx, step_obj in enumerate(steps_raw, start=1):
        if not isinstance(step_obj, dict):
            raise ValueError(f"Invalid mission file {path}: step {idx} must be an object")

        skill_id = step_obj.get("skill_id", step_obj.get("skill"))
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError(f"Invalid mission file {path}: step {idx} missing non-empty skill_id")
        if skill_id not in known_skills:
            raise ValueError(
                f"Invalid mission file {path}: step {idx} unknown skill_id '{skill_id}'"
            )

        args = step_obj.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(f"Invalid mission file {path}: step {idx} args must be an object")

        steps.append(MissionStep(skill_id=skill_id, args=args))

    title = payload.get("title", mission_id)
    if not isinstance(title, str) or not title.strip():
        raise ValueError(
            f"Invalid mission file {path}: title must be a non-empty string when provided"
        )

    goal = payload.get("goal", "")
    if not isinstance(goal, str):
        raise ValueError(f"Invalid mission file {path}: goal must be a string when provided")

    notes_raw = payload.get("notes")
    notes: str | None
    if notes_raw is None:
        notes = None
    elif isinstance(notes_raw, str):
        notes = notes_raw
    elif isinstance(notes_raw, list) and all(isinstance(item, str) for item in notes_raw):
        notes = "\n".join(notes_raw)
    else:
        raise ValueError(f"Invalid mission file {path}: notes must be a string or list of strings")

    return MissionTemplate(id=mission_id, title=title, goal=goal, steps=steps, notes=notes)


def _resolve_file_mission(mission_id: str) -> MissionTemplate | None:
    exts = (".json", ".yaml", ".yml")
    for base_dir in _mission_search_dirs():
        for ext in exts:
            candidate = base_dir / f"{mission_id}{ext}"
            if candidate.exists() and candidate.is_file():
                return _parse_mission_file(candidate, mission_id)
    return None


def _iter_file_missions(*, best_effort: bool = False) -> list[MissionTemplate]:
    exts = {".json", ".yaml", ".yml"}
    templates: dict[str, MissionTemplate] = {}

    for base_dir in _mission_search_dirs():
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        for path in sorted(base_dir.iterdir(), key=lambda p: (p.stem, p.suffix)):
            if path.suffix not in exts or not path.is_file():
                continue
            try:
                mission = _parse_mission_file(path, path.stem)
            except ValueError:
                if not best_effort:
                    raise
                log(
                    {
                        "event": "mission_file_skipped_invalid",
                        "path": str(path),
                    }
                )
                continue
            if mission.id in MISSION_TEMPLATES or mission.id in templates:
                continue
            templates[mission.id] = mission

    return [templates[key] for key in sorted(templates.keys())]


def list_missions() -> list[MissionTemplate]:
    return list(MISSION_TEMPLATES.values()) + _iter_file_missions()


def list_missions_best_effort() -> list[MissionTemplate]:
    return list(MISSION_TEMPLATES.values()) + _iter_file_missions(best_effort=True)


def get_mission(mission_id: str) -> MissionTemplate:
    try:
        return MISSION_TEMPLATES[mission_id]
    except KeyError:
        mission = _resolve_file_mission(mission_id)
        if mission is not None:
            return mission
        raise KeyError(f"Unknown mission: {mission_id}") from None


def _make_dryrun_deterministic(plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Scrub non-deterministic fields from a dry-run plan dict in-place.

    Sets capabilities_snapshot.generated_ts_ms to 0.
    Only called when --deterministic is used; default output is unchanged.
    """
    cap_snap = plan_dict.get("capabilities_snapshot")
    if isinstance(cap_snap, dict) and "generated_ts_ms" in cap_snap:
        cap_snap["generated_ts_ms"] = 0
    return plan_dict


class MissionRunner:
    def __init__(
        self,
        skill_runner,
        policy,
        require_approval_cb=None,
        *,
        redact_logs: bool = True,
        mission_log_path: Path | None = None,
    ):
        self.skill_runner = skill_runner
        self.policy = policy
        self.require_approval_cb = require_approval_cb
        self.redact_logs = redact_logs
        self.mission_log_path = (
            mission_log_path or Path.home() / "VoxeraOS" / "notes" / "mission-log.md"
        )

    def _append_mission_log(
        self,
        mission: MissionTemplate,
        outputs: list[dict[str, Any]],
        *,
        status: str,
        paused_step: int | None = None,
    ) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        summary = (
            f"- {ts} | {mission.id} | {mission.title} | status={status} | steps={len(outputs)}"
        )
        if paused_step:
            summary = f"{summary} | paused_step={paused_step}"
        if not self.redact_logs:
            details = "; ".join(
                f"step {item['step']} {item['skill']} ok={item['ok']}" for item in outputs
            )
            if details:
                summary = f"{summary} | details: {details}"
        try:
            self.mission_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.mission_log_path.open("a", encoding="utf-8") as f:
                f.write(summary + "\n")
        except Exception as exc:
            log({"event": "mission_log_error", "mission": mission.id, "error": repr(exc)})

    def simulate(
        self, mission: MissionTemplate, *, snapshot: dict[str, Any] | None = None
    ) -> PlanSimulation:
        steps: list[PlanStep] = []
        approvals_required = 0
        blocked = False
        # Collect ALL capabilities from each manifest directly. PlanStep.capability holds
        # only the primary (first sorted) capability, so multi-capability skills like
        # system.open_url (apps.open + network.change) would be underreported if we
        # derived capabilities_used from step.capability instead.
        all_capabilities: set[str] = set()

        mission_boundary_projection = {
            "filesystem": False,
            "network": False,
            "secrets": False,
            "system": False,
        }
        mission_intent_rank = {"read_only": 0, "mutating": 1, "destructive": 2}
        mission_intent = "read_only"

        for ms in mission.steps:
            manifest = self.skill_runner.registry.get(ms.skill_id)
            all_capabilities.update(manifest.capabilities)
            semantics = manifest_capability_semantics(manifest)
            boundaries = semantics.get("resource_boundaries")
            if isinstance(boundaries, dict):
                for key in mission_boundary_projection:
                    mission_boundary_projection[key] = mission_boundary_projection[key] or bool(
                        boundaries.get(key)
                    )
            intent = str(semantics.get("intent_class") or "read_only")
            if mission_intent_rank.get(intent, 0) > mission_intent_rank.get(mission_intent, 0):
                mission_intent = intent

            sim = self.skill_runner.simulate(manifest, args=ms.args, policy=self.policy)
            plan_step = sim.steps[0]
            plan_step.action = f"Run {ms.skill_id}"
            steps.append(plan_step)

            approvals_required += sim.approvals_required
            blocked = blocked or sim.blocked

        # All distinct capability strings across steps (sorted).
        capabilities_used = sorted(all_capabilities)

        # Compact snapshot metadata + mission semantic projection.
        capabilities_snapshot: dict[str, Any] = {
            "mission_semantics": {
                "intent_class": mission_intent,
                "resource_boundaries": mission_boundary_projection,
            }
        }
        if snapshot is not None:
            capabilities_snapshot = {
                **capabilities_snapshot,
                "schema_version": snapshot.get("schema_version"),
                "generated_ts_ms": snapshot.get("generated_ts_ms"),
            }

        summary = "Blocked by policy" if blocked else "Mission ready for execution"
        return PlanSimulation(
            title=f"Mission dry-run: {mission.title}",
            goal=mission.goal,
            steps=steps,
            approvals_required=approvals_required,
            blocked=blocked,
            summary=summary,
            capabilities_snapshot=capabilities_snapshot,
            capabilities_used=capabilities_used,
        )

    def run(
        self,
        mission: MissionTemplate,
        *,
        start_step: int = 1,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        context = context or {}
        log(
            {
                "event": "mission_start",
                "mission": mission.id,
                "steps": len(mission.steps),
                "start_step": start_step,
            }
        )
        outputs: list[dict[str, Any]] = []
        step_outcomes: list[dict[str, Any]] = []
        total_steps = len(mission.steps)

        for idx, ms in enumerate(mission.steps, start=1):
            if idx < start_step:
                continue

            audit_context = {"mission": mission.id, "step": idx, **context}
            step_started_at_ms = int(time.time() * 1000)
            try:
                manifest = self.skill_runner.registry.get(ms.skill_id)
                rr = self.skill_runner.run(
                    manifest,
                    args=ms.args,
                    policy=self.policy,
                    require_approval_cb=self.require_approval_cb,
                    audit_context=audit_context,
                )
            except KeyError:
                rr = RunResult(
                    ok=False,
                    error=f"Unknown skill: {ms.skill_id}",
                    data={
                        "status": "failed",
                        "error_class": "skill_not_found",
                        "retryable": False,
                        "summary": f"Unknown skill: {ms.skill_id}",
                        "next_action_hint": "replan_with_known_skill",
                        "machine_payload": {"missing_skill_id": ms.skill_id},
                    },
                )
            step_finished_at_ms = int(time.time() * 1000)
            canonical = extract_skill_result(rr.data if isinstance(rr.data, dict) else {})
            machine_payload = (
                canonical.get("machine_payload")
                if isinstance(canonical.get("machine_payload"), dict)
                else (rr.data if isinstance(rr.data, dict) else {})
            )
            outputs.append(
                {
                    "step": idx,
                    "skill": ms.skill_id,
                    "args": ms.args,
                    "ok": rr.ok,
                    "output": rr.output,
                    "machine_payload": machine_payload,
                    "started_at_ms": step_started_at_ms,
                    "finished_at_ms": step_finished_at_ms,
                    "duration_ms": max(0, step_finished_at_ms - step_started_at_ms),
                    "summary": str(canonical.get("summary") or rr.output or rr.error or "").strip(),
                    "output_artifacts": (
                        canonical.get("output_artifacts")
                        if isinstance(canonical.get("output_artifacts"), list)
                        else (
                            rr.data.get("artifacts")
                            if isinstance(rr.data, dict)
                            and isinstance(rr.data.get("artifacts"), list)
                            else []
                        )
                    ),
                    "operator_note": canonical.get("operator_note"),
                    "next_action_hint": canonical.get("next_action_hint"),
                    "retryable": (
                        canonical.get("retryable")
                        if isinstance(canonical.get("retryable"), bool)
                        else (
                            rr.data.get("retryable")
                            if isinstance(rr.data, dict)
                            and isinstance(rr.data.get("retryable"), bool)
                            else None
                        )
                    ),
                    "blocked": (
                        canonical.get("blocked")
                        if isinstance(canonical.get("blocked"), bool)
                        else None
                    ),
                    "approval_status": (
                        str(canonical.get("approval_status"))
                        if canonical.get("approval_status") is not None
                        else None
                    ),
                    "error": (
                        str(canonical.get("error"))
                        if canonical.get("error") is not None
                        else rr.error
                    ),
                    "error_class": (
                        canonical.get("error_class")
                        if canonical.get("error_class") is not None
                        else (rr.data.get("error_class") if isinstance(rr.data, dict) else None)
                    ),
                }
            )
            if rr.data.get("status") == "pending_approval":
                step_outcomes.append(
                    {
                        "step": idx,
                        "skill": ms.skill_id,
                        "outcome": "awaiting_approval",
                        "approval_status": "pending",
                    }
                )
            elif not rr.ok:
                error_class = (
                    str(canonical.get("error_class") or rr.data.get("error_class") or "")
                    .strip()
                    .lower()
                    if isinstance(rr.data, dict)
                    else str(canonical.get("error_class") or "").strip().lower()
                )
                boundary_blocked = error_class in {
                    "path_blocked_scope",
                    "capability_boundary_mismatch",
                    "policy_denied",
                }
                blocked_now = (
                    canonical.get("blocked") is True
                    or str(rr.data.get("status") or "") == "blocked"
                    or "Denied by policy" in str(rr.error or "")
                    or boundary_blocked
                )
                outcome = "blocked" if blocked_now else "failed"
                step_outcomes.append(
                    {
                        "step": idx,
                        "skill": ms.skill_id,
                        "outcome": outcome,
                        "approval_status": (
                            "denied" if "User rejected approval" in str(rr.error or "") else None
                        ),
                        "reason": rr.data.get("reason") if isinstance(rr.data, dict) else None,
                        "blocked_reason_class": (
                            rr.data.get("blocked_reason_class")
                            or (error_class if blocked_now else None)
                            if isinstance(rr.data, dict)
                            else None
                        ),
                    }
                )
            else:
                step_outcomes.append(
                    {
                        "step": idx,
                        "skill": ms.skill_id,
                        "outcome": "succeeded",
                        "approval_status": "approved"
                        if context.get("approval_resumed") and idx == start_step
                        else None,
                    }
                )
            if rr.data.get("status") == "pending_approval":
                log(
                    {
                        "event": "mission_pending_approval",
                        "mission": mission.id,
                        "step": idx,
                        "skill": ms.skill_id,
                    }
                )
                self._append_mission_log(
                    mission, outputs, status="pending_approval", paused_step=idx
                )
                data = dict(rr.data)
                data.setdefault("results", outputs)
                data.setdefault("step", idx)
                data.setdefault("skill", ms.skill_id)
                data.setdefault("step_outcomes", step_outcomes)
                data.setdefault("lifecycle_state", "awaiting_approval")
                data.setdefault("terminal_outcome", None)
                data.setdefault("current_step_index", idx)
                data.setdefault("last_completed_step", max(idx - 1, 0))
                data.setdefault("last_attempted_step", idx)
                data.setdefault("total_steps", total_steps)
                return RunResult(ok=False, error="Mission paused for approval.", data=data)
            if not rr.ok:
                log(
                    {
                        "event": "mission_error",
                        "mission": mission.id,
                        "step": idx,
                        "error": rr.error,
                    }
                )
                self._append_mission_log(mission, outputs, status="failed")
                terminal_error_class = (
                    str(canonical.get("error_class") or rr.data.get("error_class") or "")
                    .strip()
                    .lower()
                )
                blocked_terminal = (
                    canonical.get("blocked") is True
                    or str(rr.data.get("status") or "") == "blocked"
                    or "Denied by policy" in str(rr.error or "")
                    or terminal_error_class
                    in {"path_blocked_scope", "capability_boundary_mismatch", "policy_denied"}
                )
                return RunResult(
                    ok=False,
                    error=f"Mission failed at step {idx} ({ms.skill_id}): {rr.error}",
                    data={
                        "results": outputs,
                        "step_outcomes": step_outcomes,
                        "lifecycle_state": "blocked" if blocked_terminal else "step_failed",
                        "terminal_outcome": "blocked" if blocked_terminal else "failed",
                        "current_step_index": idx,
                        "last_completed_step": max(idx - 1, 0),
                        "last_attempted_step": idx,
                        "total_steps": total_steps,
                    },
                )

        log({"event": "mission_done", "mission": mission.id, "steps": len(mission.steps)})
        self._append_mission_log(mission, outputs, status="ok")
        return RunResult(
            ok=True,
            output=f"Mission completed: {mission.title}",
            data={
                "results": outputs,
                "step_outcomes": step_outcomes,
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": total_steps,
                "last_completed_step": total_steps,
                "last_attempted_step": total_steps,
                "total_steps": total_steps,
            },
        )
