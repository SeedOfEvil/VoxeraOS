from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audit import log
from ..models import PlanSimulation, PlanStep, RunResult


@dataclass(frozen=True)
class MissionStep:
    skill_id: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionTemplate:
    id: str
    title: str
    goal: str
    steps: List[MissionStep]
    notes: Optional[str] = None


MISSION_TEMPLATES: Dict[str, MissionTemplate] = {
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
            MissionStep(skill_id="files.write_text", args={"path": "~/VoxeraOS/notes/daily-checkin.txt", "text": "Today:\n- Priorities\n- Blockers\n"}),
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
            MissionStep(skill_id="files.read_text", args={"path": "~/VoxeraOS/notes/daily-checkin.txt"}),
            MissionStep(skill_id="clipboard.copy", args={"text": "Workday summary captured in ~/VoxeraOS/notes/daily-checkin.txt"}),
            MissionStep(skill_id="system.set_volume", args={"percent": "15"}),
        ],
        notes="Review notes, copy summary text, and lower volume.",
    ),
    "system_check": MissionTemplate(
        id="system_check",
        title="System Check",
        goal="Collect baseline status and verify command path",
        steps=[MissionStep(skill_id="system.status")],
        notes="Low-risk health check mission.",
    ),
}


def list_missions() -> List[MissionTemplate]:
    return list(MISSION_TEMPLATES.values())


def get_mission(mission_id: str) -> MissionTemplate:
    try:
        return MISSION_TEMPLATES[mission_id]
    except KeyError as exc:
        raise KeyError(f"Unknown mission: {mission_id}") from exc


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
        self.mission_log_path = mission_log_path or Path.home() / "VoxeraOS" / "notes" / "mission-log.md"

    def _append_mission_log(
        self,
        mission: MissionTemplate,
        outputs: List[Dict[str, Any]],
        *,
        status: str,
        paused_step: int | None = None,
    ) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        summary = f"- {ts} | {mission.id} | {mission.title} | status={status} | steps={len(outputs)}"
        if paused_step:
            summary = f"{summary} | paused_step={paused_step}"
        if not self.redact_logs:
            details = "; ".join(
                f"step {item['step']} {item['skill']} ok={item['ok']}"
                for item in outputs
            )
            if details:
                summary = f"{summary} | details: {details}"
        try:
            self.mission_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.mission_log_path.open("a", encoding="utf-8") as f:
                f.write(summary + "\n")
        except Exception as exc:
            log({"event": "mission_log_error", "mission": mission.id, "error": repr(exc)})

    def simulate(self, mission: MissionTemplate) -> PlanSimulation:
        steps: List[PlanStep] = []
        approvals_required = 0
        blocked = False

        for ms in mission.steps:
            manifest = self.skill_runner.registry.get(ms.skill_id)
            sim = self.skill_runner.simulate(manifest, args=ms.args, policy=self.policy)
            plan_step = sim.steps[0]
            plan_step.action = f"Run {ms.skill_id}"
            steps.append(plan_step)

            approvals_required += sim.approvals_required
            blocked = blocked or sim.blocked

        summary = "Blocked by policy" if blocked else "Mission ready for execution"
        return PlanSimulation(
            title=f"Mission dry-run: {mission.title}",
            goal=mission.goal,
            steps=steps,
            approvals_required=approvals_required,
            blocked=blocked,
            summary=summary,
        )

    def run(
        self,
        mission: MissionTemplate,
        *,
        start_step: int = 1,
        context: Dict[str, Any] | None = None,
    ) -> RunResult:
        context = context or {}
        log({"event": "mission_start", "mission": mission.id, "steps": len(mission.steps), "start_step": start_step})
        outputs: List[Dict[str, Any]] = []

        for idx, ms in enumerate(mission.steps, start=1):
            if idx < start_step:
                continue

            audit_context = {"mission": mission.id, "step": idx, **context}
            rr = self.skill_runner.run(
                self.skill_runner.registry.get(ms.skill_id),
                args=ms.args,
                policy=self.policy,
                require_approval_cb=self.require_approval_cb,
                audit_context=audit_context,
            )
            outputs.append(
                {
                    "step": idx,
                    "skill": ms.skill_id,
                    "ok": rr.ok,
                    "output": rr.output,
                    "error": rr.error,
                }
            )
            if rr.data.get("status") == "pending_approval":
                log({"event": "mission_pending_approval", "mission": mission.id, "step": idx, "skill": ms.skill_id})
                self._append_mission_log(mission, outputs, status="pending_approval", paused_step=idx)
                data = dict(rr.data)
                data.setdefault("results", outputs)
                data.setdefault("step", idx)
                data.setdefault("skill", ms.skill_id)
                return RunResult(ok=False, error="Mission paused for approval.", data=data)
            if not rr.ok:
                log({"event": "mission_error", "mission": mission.id, "step": idx, "error": rr.error})
                self._append_mission_log(mission, outputs, status="failed")
                return RunResult(
                    ok=False,
                    error=f"Mission failed at step {idx} ({ms.skill_id}): {rr.error}",
                    data={"results": outputs},
                )

        log({"event": "mission_done", "mission": mission.id, "steps": len(mission.steps)})
        self._append_mission_log(mission, outputs, status="ok")
        return RunResult(ok=True, output=f"Mission completed: {mission.title}", data={"results": outputs})
