from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List, Optional
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
from .core.queue_daemon import MissionQueueDaemon
from .core.mission_planner import MissionPlannerError, plan_mission

console = Console()
app = typer.Typer(help="Voxera OS — Vera's control plane CLI")

skills_app = typer.Typer(help="Manage skills")
app.add_typer(skills_app, name="skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
queue_app = typer.Typer(help="Queue job utilities")
queue_approvals_app = typer.Typer(help="Resolve pending queue approvals")
app.add_typer(missions_app, name="missions")
app.add_typer(queue_app, name="queue")
queue_app.add_typer(queue_approvals_app, name="approvals")

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
    arg: Optional[List[str]] = typer.Option(None, "--arg", help="Key=Value args (repeat --arg for multiple)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate execution without running the skill."),
):
    """Run a skill by ID (MVP)."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    mf = reg.get(skill_id)
    runner = SkillRunner(reg)

    args = {}
    for item in arg or []:
        if "=" not in item:
            raise typer.BadParameter("--arg must be key=value")
        k, v = item.split("=", 1)
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


@missions_app.command("plan")
def missions_plan(
    goal: str,
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview a cloud-planned mission without execution."),
):
    """Use the configured cloud brain to create and run a mission plan."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=cfg.policy, require_approval_cb=_approval_prompt, redact_logs=cfg.privacy.redact_logs)

    try:
        mission = asyncio.run(plan_mission(goal=goal, cfg=cfg, registry=reg, source="cli"))
    except MissionPlannerError as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[bold]Planned mission:[/bold] {mission.title}")
    console.print(f"Goal: {mission.goal}")
    console.print(f"Steps: {len(mission.steps)}")

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
    mission_runner = MissionRunner(runner, policy=cfg.policy, require_approval_cb=_approval_prompt, redact_logs=cfg.privacy.redact_logs)

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
def daemon(
    once: bool = typer.Option(False, "--once", help="Process current queue and exit."),
    queue_dir: str = typer.Option("~/VoxeraOS/notes/queue", "--queue-dir", help="Queue directory containing JSON mission jobs."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", min=0.1, help="Polling interval in seconds when watchdog is unavailable."),
    auto_approve_ask: bool = typer.Option(False, "--auto-approve-ask", help="DEV ONLY: auto-approve allowlisted ASK capabilities."),
):
    """Run mission queue daemon watching for JSON jobs."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir), poll_interval=poll_interval, auto_approve_ask=auto_approve_ask)
    try:
        daemon.run(once=once)
    except KeyboardInterrupt:
        console.print("Queue daemon stopped.")


@queue_approvals_app.command("list")
def queue_approvals_list(
    queue_dir: str = typer.Option("~/VoxeraOS/notes/queue", "--queue-dir", help="Queue directory containing JSON mission jobs."),
):
    """List pending queue approvals."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    approvals = daemon.approvals_list()
    if not approvals:
        console.print("No pending approvals.")
        return

    table = Table(title="Queue Approval Inbox")
    table.add_column("Job")
    table.add_column("Step")
    table.add_column("Skill")
    table.add_column("Capability")
    table.add_column("Reason")
    for item in approvals:
        table.add_row(
            str(item.get("job", "")),
            str(item.get("step", "")),
            str(item.get("skill", "")),
            str(item.get("capability", "")),
            str(item.get("reason", "")),
        )
    console.print(table)


@queue_app.command("status")
def queue_status(
    queue_dir: str = typer.Option("~/VoxeraOS/notes/queue", "--queue-dir", help="Queue directory containing JSON mission jobs."),
):
    """Show queue health, pending approvals, and recent failures."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=8, failed_limit=8)

    counts = status["counts"]
    counts_table = Table(title="Queue Status")
    counts_table.add_column("Bucket")
    counts_table.add_column("Count", justify="right")
    counts_table.add_row("pending/", str(counts["pending"]))
    counts_table.add_row("pending/approvals/", str(counts["pending_approvals"]))
    counts_table.add_row("done/", str(counts["done"]))
    counts_table.add_row("failed/", str(counts["failed"]))
    console.print(counts_table)

    if not status["exists"]:
        console.print(f"[yellow]Hint:[/yellow] queue root not found yet: {status['queue_root']}")

    approvals = status["pending_approvals"]
    approvals_table = Table(title="Pending Approvals")
    approvals_table.add_column("Job")
    approvals_table.add_column("Step")
    approvals_table.add_column("Skill")
    approvals_table.add_column("Reason")
    if approvals:
        for item in approvals:
            approvals_table.add_row(str(item.get("job", "")), str(item.get("step", "")), str(item.get("skill", "")), str(item.get("reason", "")))
    else:
        approvals_table.add_row("-", "-", "-", "No pending approvals")
    console.print(approvals_table)

    failed = status["recent_failed"]
    failed_table = Table(title="Recent Failed Jobs")
    failed_table.add_column("Job")
    failed_table.add_column("Error Summary")
    if failed:
        for item in failed:
            failed_table.add_row(str(item.get("job", "")), str(item.get("error", "") or "(no audit error summary)") )
    else:
        failed_table.add_row("-", "No failed jobs")
    console.print(failed_table)


@queue_approvals_app.command("approve")
def queue_approvals_approve(
    ref: str,
    queue_dir: str = typer.Option("~/VoxeraOS/notes/queue", "--queue-dir", help="Queue directory containing JSON mission jobs."),
):
    """Approve a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        ok = daemon.resolve_approval(ref, approve=True)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print("Approved and resumed." if ok else "Approval processed; job still pending another approval.")


@queue_approvals_app.command("deny")
def queue_approvals_deny(
    ref: str,
    queue_dir: str = typer.Option("~/VoxeraOS/notes/queue", "--queue-dir", help="Queue directory containing JSON mission jobs."),
):
    """Deny a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        daemon.resolve_approval(ref, approve=False)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print("Denied. Job moved to failed/.")
