from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .audit import tail
from .config import load_config
from .core.inbox import add_inbox_job, list_inbox_jobs
from .core.mission_planner import MissionPlannerError, plan_mission
from .core.missions import MissionRunner, get_mission, list_missions
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .doctor import doctor_sync
from .paths import queue_root_display
from .setup_wizard import run_setup
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner
from .version import get_version

console = Console()

RUN_ARG_OPTION = typer.Option(None, "--arg", help="Key=Value args (repeat --arg for multiple).")


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None
    return out or None


def _version_string() -> str:
    sha = _git_sha()
    version = get_version()
    return f"{version} ({sha})" if sha else version


def _show_version(value: bool):
    if not value:
        return
    console.print(_version_string())
    raise typer.Exit()


app = typer.Typer(help="Voxera OS — Vera's control plane CLI")


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show Voxera version and exit.",
    ),
):
    """Voxera CLI root command group."""


@app.command("version")
def version_cmd():
    """Show Voxera version."""
    console.print(_version_string())


skills_app = typer.Typer(help="Manage skills")
app.add_typer(skills_app, name="skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
queue_app = typer.Typer(help="Queue job utilities")
queue_approvals_app = typer.Typer(help="Resolve pending queue approvals")
inbox_app = typer.Typer(help="Human-friendly queue inbox")
app.add_typer(missions_app, name="missions")
app.add_typer(queue_app, name="queue")
app.add_typer(inbox_app, name="inbox")
queue_app.add_typer(queue_approvals_app, name="approvals")


@app.command()
def setup():
    """Run first-run typed setup wizard."""
    asyncio.run(run_setup())


@app.command()
def doctor(
    self_test: bool = typer.Option(
        False, "--self-test", help="Run queue/audit/artifact golden-path self-test."
    ),
    timeout_s: float = typer.Option(8.0, "--timeout-s", min=1.0, help="Timeout for --self-test."),
):
    """Run provider capability tests and write a report."""
    doctor_sync(self_test=self_test, timeout_s=timeout_s)


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
    table.add_column("Exec")
    table.add_column("Net")
    table.add_column("FS")
    table.add_column("Capabilities")
    for _, mf in sorted(m.items()):
        table.add_row(
            mf.id,
            mf.name,
            mf.risk,
            mf.exec_mode,
            str(mf.needs_network),
            mf.fs_scope,
            ", ".join(mf.capabilities),
        )
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
    arg: list[str] | None = RUN_ARG_OPTION,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate execution without running the skill."
    ),
):
    """Run a skill by ID (MVP)."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    mf = reg.get(skill_id)
    runner = SkillRunner(reg)
    runner.config = cfg

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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview a cloud-planned mission without execution."
    ),
):
    """Use the configured cloud brain to create and run a mission plan."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    runner.config = cfg
    mission_runner = MissionRunner(
        runner,
        policy=cfg.policy,
        require_approval_cb=_approval_prompt,
        redact_logs=cfg.privacy.redact_logs,
    )

    try:
        mission = asyncio.run(plan_mission(goal=goal, cfg=cfg, registry=reg, source="cli"))
    except MissionPlannerError as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1) from e

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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate mission execution without running skills."
    ),
):
    """Run a built-in multi-step mission by ID."""
    cfg = load_config()
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    runner.config = cfg
    mission_runner = MissionRunner(
        runner,
        policy=cfg.policy,
        require_approval_cb=_approval_prompt,
        redact_logs=cfg.privacy.redact_logs,
    )

    try:
        mission = get_mission(mission_id)
    except KeyError as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1) from e

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
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
    poll_interval: float = typer.Option(
        1.0,
        "--poll-interval",
        min=0.1,
        help="Polling interval in seconds when watchdog is unavailable.",
    ),
    auto_approve_ask: bool = typer.Option(
        False, "--auto-approve-ask", help="DEV ONLY: auto-approve allowlisted ASK capabilities."
    ),
):
    """Run mission queue daemon watching for JSON jobs."""
    daemon = MissionQueueDaemon(
        queue_root=Path(queue_dir), poll_interval=poll_interval, auto_approve_ask=auto_approve_ask
    )
    try:
        daemon.run(once=once)
    except QueueLockError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        console.print("Queue daemon stopped.")


@queue_approvals_app.command("list")
def queue_approvals_list(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """List pending queue approvals."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    approvals = daemon.approvals_list()
    if not approvals:
        console.print("No pending approvals.")
        return

    table = Table(title="Queue Approval Inbox")
    table.add_column("Job")
    table.add_column("Approve As")
    table.add_column("Step")
    table.add_column("Skill")
    table.add_column("Capability")
    table.add_column("Reason")
    table.add_column("Target")
    table.add_column("Scope")
    for item in approvals:
        target = item.get("target", {}) if isinstance(item.get("target"), dict) else {}
        scope = item.get("scope", {}) if isinstance(item.get("scope"), dict) else {}
        table.add_row(
            str(item.get("job", "")),
            " | ".join(str(v) for v in item.get("approve_refs", [])[:2]),
            str(item.get("step", "")),
            str(item.get("skill", "")),
            str(item.get("capability", "")),
            str(item.get("policy_reason", item.get("reason", ""))),
            f"{target.get('type', 'unknown')}: {target.get('value', '')}",
            f"fs={scope.get('fs_scope', '-')}, net={scope.get('needs_network', False)}",
        )
    console.print(table)


@queue_app.command("init")
def queue_init(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Create queue directories (safe mkdir -p; does not delete data)."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    daemon.ensure_dirs()
    console.print(f"Initialized queue directories under: {daemon.queue_root}")
    console.print(f"- inbox/: {daemon.inbox}")
    console.print(f"- pending/: {daemon.pending}")
    console.print(f"- pending/approvals/: {daemon.approvals}")
    console.print(f"- done/: {daemon.done}")
    console.print(f"- failed/: {daemon.failed}")
    console.print(f"- artifacts/: {daemon.artifacts}")


@queue_app.command("status")
def queue_status(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Show queue health, pending approvals, and recent failures."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=8, failed_limit=8)

    counts = status["counts"]
    counts_table = Table(title="Queue Status")
    counts_table.add_column("Bucket")
    counts_table.add_column("Count", justify="right")
    counts_table.add_row("inbox/", str(counts["inbox"]))
    counts_table.add_row("pending/", str(counts["pending"]))
    counts_table.add_row("pending/approvals/", str(counts["pending_approvals"]))
    counts_table.add_row("done/", str(counts["done"]))
    counts_table.add_row("failed/", str(counts["failed"]))
    counts_table.add_row("failed metadata valid", str(status.get("failed_sidecars_valid", 0)))
    counts_table.add_row("failed metadata invalid", str(status.get("failed_sidecars_invalid", 0)))
    counts_table.add_row("failed metadata missing", str(status.get("failed_sidecars_missing", 0)))
    retention = status.get("failed_retention", {})
    counts_table.add_row(
        "failed retention max age (s)",
        str(retention.get("max_age_s")) if retention.get("max_age_s") is not None else "(unset)",
    )
    counts_table.add_row(
        "failed retention max count",
        str(retention.get("max_count")) if retention.get("max_count") is not None else "(unset)",
    )
    console.print(counts_table)
    console.print(f"Queue intake: {status.get('intake_glob', '')}")
    console.print(f"Daemon paused: {status.get('paused', False)}")

    prune = status.get("failed_prune_last", {})
    prune_table = Table(title="Failed Retention (latest prune event)")
    prune_table.add_column("Field")
    prune_table.add_column("Value")
    prune_table.add_row("removed jobs", str(prune.get("removed_jobs", 0)))
    prune_table.add_row("removed sidecars", str(prune.get("removed_sidecars", 0)))
    prune_table.add_row(
        "event max age (s)",
        str(prune.get("max_age_s")) if prune.get("max_age_s") is not None else "(n/a)",
    )
    prune_table.add_row(
        "event max count",
        str(prune.get("max_count")) if prune.get("max_count") is not None else "(n/a)",
    )
    console.print(prune_table)
    console.print(f"Artifacts root: {status.get('artifacts_root', '')}")

    if not status["exists"]:
        console.print(f"[yellow]Hint:[/yellow] queue root not found yet: {status['queue_root']}")

    approvals = status["pending_approvals"]
    approvals_table = Table(title="Pending Approvals")
    approvals_table.add_column("Job")
    approvals_table.add_column("Step")
    approvals_table.add_column("Skill")
    approvals_table.add_column("Reason")
    approvals_table.add_column("Target")
    approvals_table.add_column("Scope")
    if approvals:
        for item in approvals:
            target = item.get("target", {}) if isinstance(item.get("target"), dict) else {}
            scope = item.get("scope", {}) if isinstance(item.get("scope"), dict) else {}
            approvals_table.add_row(
                str(item.get("job", "")),
                str(item.get("step", "")),
                str(item.get("skill", "")),
                str(item.get("policy_reason", item.get("reason", ""))),
                f"{target.get('type', 'unknown')}: {target.get('value', '')}",
                f"fs={scope.get('fs_scope', '-')}, net={scope.get('needs_network', False)}",
            )
    else:
        approvals_table.add_row("-", "-", "-", "No pending approvals", "-", "-")
    console.print(approvals_table)

    failed = status["recent_failed"]
    failed_table = Table(title="Recent Failed Jobs")
    failed_table.add_column("Job")
    failed_table.add_column("Error Summary")
    if failed:
        for item in failed:
            failed_table.add_row(
                str(item.get("job", "")), str(item.get("error", "") or "(no audit error summary)")
            )
    else:
        failed_table.add_row("-", "No failed jobs")
    console.print(failed_table)


@queue_app.command("cancel")
def queue_cancel(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Cancel a queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        moved = daemon.cancel_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Cancelled: {moved.name} (moved to failed/)")


@queue_app.command("retry")
def queue_retry(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Retry a failed queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        moved = daemon.retry_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Re-queued: {moved.name} (inbox/)")




@queue_app.command("unlock")
def queue_unlock(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Force-remove the daemon lock file for recovery workflows."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    if daemon.force_unlock():
        console.print("Removed stale daemon lock.")
        return
    console.print("No daemon lock was present.")

@queue_app.command("pause")
def queue_pause(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Pause queue processing."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    daemon.pause()
    console.print("Queue processing paused.")


@queue_app.command("resume")
def queue_resume(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Resume queue processing."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    daemon.resume()
    console.print("Queue processing resumed.")


@queue_approvals_app.command("approve")
def queue_approvals_approve(
    ref: str,
    always: bool = typer.Option(
        False, "--always", help="Approve and grant always-allow for this skill+scope."
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Approve a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        ok = daemon.resolve_approval(ref, approve=True, approve_always=always)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        "Approved and resumed." if ok else "Approval processed; job still pending another approval."
    )


@queue_approvals_app.command("deny")
def queue_approvals_deny(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Deny a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    try:
        daemon.resolve_approval(ref, approve=False)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print("Denied. Job moved to failed/.")


@inbox_app.command("add")
def inbox_add(
    goal: str,
    id: str | None = typer.Option(
        None, "--id", help="Optional job id (defaults to generated timestamp+hash)."
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Create an inbox queue job from plain goal text."""
    try:
        created = add_inbox_job(Path(queue_dir), goal, job_id=id)
    except (ValueError, FileExistsError) as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    payload = json.loads(created.read_text(encoding="utf-8"))
    console.print(f"Created inbox job: {created}")
    console.print(f"ID: {payload.get('id', '')}")
    console.print(f"Goal: {payload.get('goal', '')}")


@inbox_app.command("list")
def inbox_list(
    n: int = typer.Option(20, "--n", min=1, help="Number of recent inbox jobs to show."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """List inbox-created jobs across queue states."""
    jobs, missing_dirs = list_inbox_jobs(Path(queue_dir), limit=n)

    table = Table(title="Inbox Jobs")
    table.add_column("State")
    table.add_column("Job")
    table.add_column("ID")
    table.add_column("Goal")
    if jobs:
        for job in jobs:
            table.add_row(job.state, job.filename, job.job_id, job.goal)
    else:
        table.add_row("-", "-", "-", "No inbox jobs found")
    console.print(table)

    for missing in missing_dirs:
        console.print(f"[yellow]Hint:[/yellow] missing directory: {missing}")
