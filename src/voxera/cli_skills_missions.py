from __future__ import annotations

import asyncio
import json
import os
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
from .skills.runner import SkillRunner, is_skill_read_only


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


def _is_dev_mode() -> bool:
    """Return True when the VOXERA_DEV_MODE environment variable is truthy."""
    return os.environ.get("VOXERA_DEV_MODE", "").strip().lower() in {"1", "true", "yes"}


def run_impl(
    *,
    load_config: Callable[[], AppConfig],
    skill_registry_cls: type[SkillRegistry],
    skill_runner_cls: type[SkillRunner],
    approval_prompt: Callable,
    skill_id: str,
    arg: list[str] | None,
    dry_run: bool,
    allow_direct_mutation: bool = False,
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

    # ── Queue-first mutation gate ──────────────────────────────────────
    # Direct CLI execution is allowed for read-only skills.  Mutating
    # skills must go through the governed queue path unless the operator
    # explicitly opts in via --allow-direct-mutation in dev mode.
    if not is_skill_read_only(manifest):
        if allow_direct_mutation and _is_dev_mode():
            console.print(
                f"[yellow]WARNING:[/yellow] Running mutating skill [bold]{manifest.id}[/bold] "
                "directly (dev-mode override). "
                "In production, use the governed queue path instead."
            )
        elif allow_direct_mutation and not _is_dev_mode():
            console.print(
                f"[red]BLOCKED:[/red] --allow-direct-mutation requires VOXERA_DEV_MODE=1.\n"
                f"  Skill [bold]{manifest.id}[/bold] is mutating "
                f"(effect classes: {_effect_classes_for(manifest)}).\n"
                f"  Set VOXERA_DEV_MODE=1 to enable the dev-only override, or\n"
                f"  submit via the governed queue path:\n"
                f"    voxera queue submit --goal '<your goal>'"
            )
            raise typer.Exit(code=1)
        else:
            console.print(
                f"[red]BLOCKED:[/red] Direct CLI execution of mutating skill "
                f"[bold]{manifest.id}[/bold] is not allowed.\n"
                f"  Effect classes: {_effect_classes_for(manifest)}\n"
                f"  The queue-first governance model requires mutating skills to be\n"
                f"  submitted through the governed queue path:\n"
                f"    voxera queue submit --goal '<your goal>'\n"
                f"  For development, use: VOXERA_DEV_MODE=1 voxera run {manifest.id} "
                f"--allow-direct-mutation"
            )
            raise typer.Exit(code=1)

    result = runner.run(manifest, args=args, policy=cfg.policy, require_approval_cb=approval_prompt)
    if result.ok:
        console.print(result.output or "OK")
    else:
        console.print(f"[red]ERROR:[/red] {result.error}")
        raise typer.Exit(code=1)


def _effect_classes_for(manifest) -> str:
    """Return a human-readable summary of effect classes for a manifest."""
    from .policy import CAPABILITY_EFFECT_CLASS

    classes = sorted({CAPABILITY_EFFECT_CLASS.get(c, "unknown") for c in manifest.capabilities})
    return ", ".join(classes) if classes else "unknown"


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
