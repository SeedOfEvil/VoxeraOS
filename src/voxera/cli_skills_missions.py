from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import typer
from rich.table import Table

from .cli_common import console
from .config import AppConfig
from .core.capabilities_snapshot import (
    generate_capabilities_snapshot,
    validate_mission_id_against_snapshot,
    validate_mission_steps_against_snapshot,
)
from .core.mission_planner import MissionPlannerError, plan_mission
from .core.missions import MissionRunner, _make_dryrun_deterministic, get_mission, list_missions
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner


def approval_prompt_impl(manifest, decision):
    console.print(f"\n⚠️  Approval required for: [bold]{manifest.id}[/bold]")
    console.print(f"Reason: {decision.reason}")
    return typer.confirm("Approve?", default=False)


def skills_list_impl(*, skill_registry_cls: type[SkillRegistry]) -> None:
    reg = skill_registry_cls()
    manifests = reg.discover()
    table = Table(title="Skills")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Risk")
    table.add_column("Exec")
    table.add_column("Net")
    table.add_column("FS")
    table.add_column("Capabilities")
    for _, manifest in sorted(manifests.items()):
        table.add_row(
            manifest.id,
            manifest.name,
            manifest.risk,
            manifest.exec_mode,
            str(manifest.needs_network),
            manifest.fs_scope,
            ", ".join(manifest.capabilities),
        )
    console.print(table)


def run_impl(
    *,
    load_config: Callable[[], AppConfig],
    skill_registry_cls: type[SkillRegistry],
    skill_runner_cls: type[SkillRunner],
    approval_prompt: Callable,
    skill_id: str,
    arg: list[str] | None,
    dry_run: bool,
) -> None:
    cfg = load_config()
    reg = skill_registry_cls()
    reg.discover()
    manifest = reg.get(skill_id)
    runner = skill_runner_cls(reg)
    runner.config = cfg

    args = {}
    for item in arg or []:
        if "=" not in item:
            raise typer.BadParameter("--arg must be key=value")
        key, value = item.split("=", 1)
        args[key] = value

    if dry_run:
        sim = runner.simulate(manifest, args=args, policy=cfg.policy)
        console.print(json.dumps(sim.model_dump(), indent=2))
        return

    result = runner.run(manifest, args=args, policy=cfg.policy, require_approval_cb=approval_prompt)
    if result.ok:
        console.print(result.output or "OK")
    else:
        console.print(f"[red]ERROR:[/red] {result.error}")
        raise typer.Exit(code=1)


def missions_list_impl() -> None:
    table = Table(title="Missions")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Steps")
    table.add_column("Notes")
    for mission in sorted(list_missions(), key=lambda item: item.id):
        table.add_row(mission.id, mission.title, str(len(mission.steps)), mission.notes or "")
    console.print(table)


def missions_plan_impl(
    *,
    load_config: Callable[[], AppConfig],
    skill_registry_cls: type[SkillRegistry],
    skill_runner_cls: type[SkillRunner],
    approval_prompt: Callable,
    goal: str,
    dry_run: bool,
    freeze_capabilities_snapshot: bool,
    deterministic: bool,
) -> None:
    if (deterministic or freeze_capabilities_snapshot) and not dry_run:
        console.print(
            "[red]ERROR:[/red] --deterministic and --freeze-capabilities-snapshot require --dry-run."
        )
        raise typer.Exit(code=1)

    cfg = load_config()
    reg = skill_registry_cls()
    reg.discover()
    runner = skill_runner_cls(reg)
    runner.config = cfg
    mission_runner = MissionRunner(
        runner,
        policy=cfg.policy,
        require_approval_cb=approval_prompt,
        redact_logs=cfg.privacy.redact_logs,
    )

    snapshot = generate_capabilities_snapshot(reg) if freeze_capabilities_snapshot else None

    try:
        mission = asyncio.run(plan_mission(goal=goal, cfg=cfg, registry=reg, source="cli"))
        if snapshot is None:
            snapshot = generate_capabilities_snapshot(reg)
        validate_mission_steps_against_snapshot(mission, snapshot)
    except (MissionPlannerError, ValueError) as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold]Planned mission:[/bold] {mission.title}")
    console.print(f"Goal: {mission.goal}")
    console.print(f"Steps: {len(mission.steps)}")

    if dry_run:
        sim = mission_runner.simulate(mission, snapshot=snapshot)
        out = sim.model_dump()
        if deterministic:
            _make_dryrun_deterministic(out)
        console.print(json.dumps(out, indent=2, sort_keys=True))
        return

    result = mission_runner.run(mission)
    if result.ok:
        console.print(result.output)
    else:
        console.print(f"[red]ERROR:[/red] {result.error}")
        raise typer.Exit(code=1)


def missions_run_impl(
    *,
    load_config: Callable[[], AppConfig],
    skill_registry_cls: type[SkillRegistry],
    skill_runner_cls: type[SkillRunner],
    approval_prompt: Callable,
    mission_id: str,
    dry_run: bool,
) -> None:
    cfg = load_config()
    reg = skill_registry_cls()
    reg.discover()
    runner = skill_runner_cls(reg)
    runner.config = cfg
    mission_runner = MissionRunner(
        runner,
        policy=cfg.policy,
        require_approval_cb=approval_prompt,
        redact_logs=cfg.privacy.redact_logs,
    )

    try:
        snapshot = generate_capabilities_snapshot(reg)
        validate_mission_id_against_snapshot(mission_id, snapshot)
        mission = get_mission(mission_id)
        validate_mission_steps_against_snapshot(mission, snapshot)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        sim = mission_runner.simulate(mission, snapshot=snapshot)
        console.print(json.dumps(sim.model_dump(), indent=2, sort_keys=True))
        return

    result = mission_runner.run(mission)
    if result.ok:
        console.print(result.output)
    else:
        console.print(f"[red]ERROR:[/red] {result.error}")
        raise typer.Exit(code=1)
