from __future__ import annotations

from dataclasses import dataclass, field
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
    def __init__(self, skill_runner, policy, require_approval_cb=None):
        self.skill_runner = skill_runner
        self.policy = policy
        self.require_approval_cb = require_approval_cb

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

    def run(self, mission: MissionTemplate) -> RunResult:
        log({"event": "mission_start", "mission": mission.id, "steps": len(mission.steps)})
        outputs: List[Dict[str, Any]] = []

        for idx, ms in enumerate(mission.steps, start=1):
            manifest = self.skill_runner.registry.get(ms.skill_id)
            rr = self.skill_runner.run(
                manifest,
                args=ms.args,
                policy=self.policy,
                require_approval_cb=self.require_approval_cb,
                audit_context={"mission": mission.id, "step": idx},
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
            if not rr.ok:
                log({"event": "mission_error", "mission": mission.id, "step": idx, "error": rr.error})
                return RunResult(
                    ok=False,
                    error=f"Mission failed at step {idx} ({ms.skill_id}): {rr.error}",
                    data={"results": outputs},
                )

        log({"event": "mission_done", "mission": mission.id, "steps": len(mission.steps)})
        return RunResult(ok=True, output=f"Mission completed: {mission.title}", data={"results": outputs})
