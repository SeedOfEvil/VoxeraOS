from __future__ import annotations

import asyncio
import json
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from .config import load_config
from .setup_wizard import run_setup
from .doctor import doctor_sync
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner
from .audit import tail
from .core.missions import MissionRunner, get_mission, list_missions

console = Console()
app = typer.Typer(help="Voxera OS — Vera's control plane CLI")

skills_app = typer.Typer(help="Manage skills")
app.add_typer(skills_app, name="skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
app.add_typer(missions_app, name="missions")

@app.command()
def setup():
    """Run first-run typed setup wizard."""
    asyncio.run(run_setup())

@app.command()
def doctor():
    """Run provider capability tests and write a report."""
    doctor_sync()

@app.command()
def status():
    """Show current configuration summary."""
    cfg = load_config()
    table = Table(title="Voxera Status")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("mode", cfg.mode)
    table.add_row("cloud_allowed", str(cfg.privacy.cloud_allowed))
    table.add_row("redact_logs", str(cfg.privacy.redact_logs))
    table.add_row("brains", ", ".join(cfg.brain.keys()) if cfg.brain else "(not configured)")
    console.print(table)

@skills_app.command("list")
def skills_list():
    reg = SkillRegistry()
    m = reg.discover()
    table = Table(title="Skills")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Risk")
    table.add_column("Capabilities")
    for _, mf in sorted(m.items()):
        table.add_row(mf.id, mf.name, mf.risk, ", ".join(mf.capabilities))
    console.print(table)


@missions_app.command("list")
def missions_list():
    table = Table(title="Missions")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Steps")
    table.add_column("Notes")
    for mission in sorted(list_missions(), key=lambda m: m.id):
        table.add_row(mission.id, mission.title, str(len(mission.steps)), mission.notes or "")
    console.print(table)

def _approval_prompt(manifest, decision):
    console.print(f"\n⚠️  Approval required for: [bold]{manifest.id}[/bold]")
    console.print(f"Reason: {decision.reason}")
    return typer.confirm("Approve?", default=False)

@app.command()
def run(
    skill_id: str,
    arg: Optional[str] = typer.Option(None, help="Key=Value arg (single MVP)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate execution without running the skill."),
):
    """Run a skill by ID (MVP)."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    mf = reg.get(skill_id)
    runner = SkillRunner(reg)

    args = {}
    if arg:
        if "=" not in arg:
            raise typer.BadParameter("--arg must be key=value")
        k, v = arg.split("=", 1)
        args[k] = v

    if dry_run:
        sim = runner.simulate(mf, args=args, policy=cfg.policy)
        console.print(json.dumps(sim.model_dump(), indent=2))
        return

    rr = runner.run(mf, args=args, policy=cfg.policy, require_approval_cb=_approval_prompt)
    if rr.ok:
        console.print(rr.output or "OK")
    else:
        console.print(f"[red]ERROR:[/red] {rr.error}")
        raise typer.Exit(code=1)


@missions_app.command("run")
def missions_run(
    mission_id: str,
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate mission execution without running skills."),
):
    """Run a built-in multi-step mission by ID."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=cfg.policy, require_approval_cb=_approval_prompt)

    try:
        mission = get_mission(mission_id)
    except KeyError as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1)

    if dry_run:
        sim = mission_runner.simulate(mission)
        console.print(json.dumps(sim.model_dump(), indent=2))
        return

    rr = mission_runner.run(mission)
    if rr.ok:
        console.print(rr.output)
    else:
        console.print(f"[red]ERROR:[/red] {rr.error}")
        raise typer.Exit(code=1)

@app.command()
def audit(n: int = 30):
    """Show last N audit events."""
    events = tail(n)
    for e in events:
        console.print(e)

@app.command()
def panel(host: str = "127.0.0.1", port: int = 8844):
    """Run the minimal approvals/audit panel."""
    import uvicorn
    uvicorn.run("voxera.panel.app:app", host=host, port=port, reload=False)

@app.command()
def daemon():
    """Placeholder for a long-running core daemon (router/planner/event loop)."""
    console.print("Voxera daemon scaffold. For now, use 'voxera run' and 'voxera panel'.")
