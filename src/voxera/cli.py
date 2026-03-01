from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .audit import tail
from .config import load_app_config as load_config
from .config import load_config as load_runtime_config
from .config import (
    load_runtime_env,
    should_load_dotenv,
    write_config_fingerprint,
    write_config_snapshot,
)
from .core.artifacts import format_bytes, prune_artifacts
from .core.capabilities_snapshot import (
    generate_capabilities_snapshot,
    validate_mission_id_against_snapshot,
    validate_mission_steps_against_snapshot,
)
from .core.inbox import add_inbox_job, list_inbox_jobs
from .core.mission_planner import MissionPlannerError, plan_mission
from .core.missions import MissionRunner, _make_dryrun_deterministic, get_mission, list_missions
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .core.queue_hygiene import TERMINAL_BUCKETS, prune_queue_buckets
from .doctor import doctor_sync
from .incident_bundle import BundleError, build_job_bundle, build_system_bundle
from .ops_bundle import build_job_bundle as build_ops_job_bundle
from .ops_bundle import build_system_bundle as build_ops_system_bundle
from .paths import queue_root_display
from .setup_wizard import run_setup
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner
from .version import get_version

console = Console()

RUN_ARG_OPTION = typer.Option(None, "--arg", help="Key=Value args (repeat --arg for multiple).")
OUT_PATH_OPTION = typer.Option(..., "--out", help="Output zip file path.")
OPS_BUNDLE_ARCHIVE_DIR_OPTION = typer.Option(
    None,
    "--dir",
    help="Archive directory for ops bundle outputs. Defaults to VOXERA_OPS_BUNDLE_DIR or notes/queue/_archive/<timestamp>/.",
)
SNAPSHOT_PATH_OPTION = typer.Option(
    None,
    "--path",
    "--out",
    help="Snapshot output file path. Defaults to <queue_root>/_ops/config_snapshot.json.",
)


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
    if should_load_dotenv():
        load_runtime_env()
        load_runtime_env(Path(".env"))


@app.command("version")
def version_cmd():
    """Show Voxera version."""
    console.print(_version_string())


config_app = typer.Typer(help="Runtime configuration utilities")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show resolved runtime config (redacted)."""
    cfg = load_runtime_config()
    typer.echo(json.dumps(cfg.to_safe_dict(), sort_keys=True))


@app.command("config-show")
def config_show_legacy():
    """Backward-compatible alias for `voxera config show`."""
    cfg = load_runtime_config()
    typer.echo(json.dumps(cfg.to_safe_dict(), sort_keys=True))


@config_app.command("snapshot")
def config_snapshot(path: Path | None = SNAPSHOT_PATH_OPTION) -> None:
    """Write a redacted runtime config snapshot and print its absolute path."""
    cfg = load_runtime_config()
    target = (
        path.expanduser().resolve()
        if path is not None
        else cfg.queue_root / "_ops" / "config_snapshot.json"
    )
    written = write_config_snapshot(target.parent, cfg, filename=target.name)
    write_config_fingerprint(cfg.queue_root, cfg)
    typer.echo(str(written.resolve()))


@config_app.command("validate")
def config_validate():
    """Validate runtime config and exit non-zero on errors."""
    try:
        cfg = load_runtime_config()
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"status": "ok", "config_path": str(cfg.config_path)}, sort_keys=True))


artifacts_app = typer.Typer(help="Artifact management utilities")
app.add_typer(artifacts_app, name="artifacts")
skills_app = typer.Typer(help="Manage skills")
app.add_typer(skills_app, name="skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
queue_app = typer.Typer(help="Queue job utilities")
queue_approvals_app = typer.Typer(help="Resolve pending queue approvals")
queue_lock_app = typer.Typer(help="Queue daemon lock utilities")
ops_app = typer.Typer(help="Operational incident bundle utilities")
ops_bundle_app = typer.Typer(help="Export operator bundles")
inbox_app = typer.Typer(help="Human-friendly queue inbox")
app.add_typer(missions_app, name="missions")
app.add_typer(queue_app, name="queue")
app.add_typer(ops_app, name="ops")
app.add_typer(inbox_app, name="inbox")
queue_app.add_typer(queue_approvals_app, name="approvals")
queue_app.add_typer(queue_lock_app, name="lock")
ops_app.add_typer(ops_bundle_app, name="bundle")


@app.command()
def setup():
    """Run first-run typed setup wizard."""
    asyncio.run(run_setup())


@app.command()
def doctor(
    self_test: bool = typer.Option(
        False, "--self-test", help="Run queue/audit/artifact golden-path self-test."
    ),
    quick: bool = typer.Option(False, "--quick", help="Run fast offline checks only."),
    timeout_s: float = typer.Option(8.0, "--timeout-s", min=1.0, help="Timeout for --self-test."),
):
    """Run provider capability tests and write a report."""
    doctor_sync(self_test=self_test, timeout_s=timeout_s, quick=quick)


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
    freeze_capabilities_snapshot: bool = typer.Option(
        False,
        "--freeze-capabilities-snapshot",
        help=(
            "Guarantee the capabilities snapshot is generated once per invocation "
            "and reused throughout the planning path (dry-run only)."
        ),
    ),
    deterministic: bool = typer.Option(
        False,
        "--deterministic",
        help=(
            "Scrub timestamps from dry-run JSON output for byte-identical CI/golden-test output. "
            "Sets capabilities_snapshot.generated_ts_ms=0. Dry-run only."
        ),
    ),
):
    """Use the configured cloud brain to create and run a mission plan."""
    if (deterministic or freeze_capabilities_snapshot) and not dry_run:
        console.print(
            "[red]ERROR:[/red] --deterministic and --freeze-capabilities-snapshot require --dry-run."
        )
        raise typer.Exit(code=1)

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

    snapshot = generate_capabilities_snapshot(reg) if freeze_capabilities_snapshot else None

    try:
        mission = asyncio.run(plan_mission(goal=goal, cfg=cfg, registry=reg, source="cli"))
        if snapshot is None:
            snapshot = generate_capabilities_snapshot(reg)
        validate_mission_steps_against_snapshot(mission, snapshot)
    except (MissionPlannerError, ValueError) as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1) from e

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
        snapshot = generate_capabilities_snapshot(reg)
        validate_mission_id_against_snapshot(mission_id, snapshot)
        mission = get_mission(mission_id)
        validate_mission_steps_against_snapshot(mission, snapshot)
    except (KeyError, ValueError) as e:
        console.print(f"[red]ERROR:[/red] {e}")
        raise typer.Exit(code=1) from e

    if dry_run:
        sim = mission_runner.simulate(mission, snapshot=snapshot)
        console.print(json.dumps(sim.model_dump(), indent=2, sort_keys=True))
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
def panel(
    host: str | None = typer.Option(None, "--host", help="Panel host override."),
    port: int | None = typer.Option(None, "--port", help="Panel port override."),
):
    """Run the minimal approvals/audit panel."""
    import uvicorn

    runtime_cfg = load_runtime_config(overrides={"panel_host": host, "panel_port": port})
    uvicorn.run(
        "voxera.panel.app:app",
        host=runtime_cfg.panel_host,
        port=runtime_cfg.panel_port,
        reload=False,
    )


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


@queue_app.command("bundle")
def queue_bundle(
    job_id: str | None = typer.Argument(None),
    system: bool = typer.Option(False, "--system", help="Export overall system bundle."),
    out: Path = OUT_PATH_OPTION,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Export a deterministic incident bundle for a job or the whole system."""
    root = Path(queue_dir)
    if system:
        data = build_system_bundle(root)
    else:
        if not job_id:
            raise typer.BadParameter("Provide <job_id> or use --system")
        try:
            data = build_job_bundle(root, job_id)
        except BundleError as exc:
            console.print(f"[red]ERROR:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    console.print(f"Bundle written: {out}")


@ops_app.command("capabilities")
def ops_capabilities():
    """Print runtime capabilities snapshot JSON."""
    reg = SkillRegistry()
    snapshot = generate_capabilities_snapshot(reg)
    typer.echo(json.dumps(snapshot, sort_keys=True))


@ops_bundle_app.command("system")
def ops_bundle_system(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
    archive_dir: Path | None = OPS_BUNDLE_ARCHIVE_DIR_OPTION,
):
    """Export a system ops bundle."""
    queue_root = Path(queue_dir).expanduser().resolve()
    out = build_ops_system_bundle(
        queue_root,
        archive_dir=archive_dir,
        prefer_queue_root_archive=True,
    )
    typer.echo(str(out.resolve()))


@ops_bundle_app.command("job")
def ops_bundle_job(
    job_ref: str = typer.Argument(..., help="Job file name/reference."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
    archive_dir: Path | None = OPS_BUNDLE_ARCHIVE_DIR_OPTION,
):
    """Export a per-job ops bundle."""
    queue_root = Path(queue_dir).expanduser().resolve()
    out = build_ops_job_bundle(
        queue_root,
        job_ref,
        archive_dir=archive_dir,
        prefer_queue_root_archive=True,
    )
    typer.echo(str(out.resolve()))


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
    console.print(f"- canceled/: {daemon.canceled}")
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
    counts_table.add_row("canceled/", str(counts.get("canceled", 0)))
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

    lock_counters = status.get("daemon_lock_counters", {})
    lock_table = Table(title="Daemon Lock Counters")
    lock_table.add_column("Event")
    lock_table.add_column("Count", justify="right")
    lock_table.add_row("acquire ok", str(lock_counters.get("lock_acquire_ok", 0)))
    lock_table.add_row("acquire fail", str(lock_counters.get("lock_acquire_fail", 0)))
    lock_table.add_row("reclaimed", str(lock_counters.get("lock_reclaimed", 0)))
    lock_table.add_row("released", str(lock_counters.get("lock_released", 0)))
    lock_table.add_row("unlock refused", str(lock_counters.get("unlock_refused", 0)))
    lock_table.add_row("unlock ok", str(lock_counters.get("unlock_ok", 0)))
    lock_table.add_row("force unlock", str(lock_counters.get("force_unlock_count", 0)))
    console.print(lock_table)

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


def _render_lock_status(status: dict[str, Any]) -> None:
    lock = status.get("lock_status", {}) if isinstance(status.get("lock_status"), dict) else {}
    lock_table = Table(title="Lock Status")
    lock_table.add_column("Field")
    lock_table.add_column("Value")
    lock_table.add_row("lock path", str(lock.get("lock_path", "")))
    lock_table.add_row("lock exists", str(lock.get("exists", False)))
    lock_table.add_row("lock pid", str(lock.get("pid", 0)))
    lock_table.add_row("lock pid alive", str(lock.get("alive", False)))
    console.print(lock_table)


@queue_lock_app.command("status")
def queue_lock_status(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Show queue daemon lock status table."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=3, failed_limit=3)
    _render_lock_status(status)


@queue_app.command("health")
def queue_health(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Show daemon/panel health counters and lock status."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=3, failed_limit=3)

    console.print(f"Health snapshot: {status.get('health_path', '')}")
    console.print(f"Queue intake: {status.get('intake_glob', '')}")
    console.print(f"Daemon paused: {status.get('paused', False)}")
    console.print(f"Daemon started at ms: {status.get('daemon_started_at_ms')}")
    console.print(f"Daemon pid: {status.get('daemon_pid')}")
    console.print(f"Last ok event: {status.get('last_ok_event', '')}")
    console.print(f"Last ok ts ms: {status.get('last_ok_ts_ms')}")
    console.print(f"Last error: {status.get('last_error', '')}")
    console.print(f"Last error ts ms: {status.get('last_error_ts_ms')}")

    _render_lock_status(status)

    counters = (
        status.get("daemon_lock_counters", {})
        if isinstance(status.get("daemon_lock_counters"), dict)
        else {}
    )
    health_table = Table(title="Health Counters")
    health_table.add_column("Counter")
    health_table.add_column("Value", justify="right")
    for key in [
        "lock_acquire_ok",
        "lock_acquire_fail",
        "lock_reclaimed",
        "lock_released",
        "unlock_refused",
        "unlock_ok",
        "force_unlock_count",
        "panel_mutation_allowed",
        "panel_401_count",
        "panel_403_count",
        "panel_csrf_missing",
        "panel_csrf_invalid",
        "panel_auth_invalid",
        "brain_fallback_count",
        "brain_fallback_reason_timeout",
        "brain_fallback_reason_auth",
        "brain_fallback_reason_rate_limit",
        "brain_fallback_reason_malformed",
        "brain_fallback_reason_network",
        "brain_fallback_reason_unknown",
    ]:
        health_table.add_row(key, str(counters.get(key, 0)))
    console.print(health_table)


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
    console.print(f"Cancelled: {moved.name} (moved to canceled/)")


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
    force: bool = typer.Option(
        False,
        "--force",
        help="Force-remove lock even if held by a live daemon (dangerous).",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Remove stale/dead daemon lock, or force-remove with --force."""
    daemon = MissionQueueDaemon(queue_root=Path(queue_dir))
    if force:
        if daemon.force_unlock():
            console.print("Force-removed daemon lock.")
            return
        console.print("No daemon lock was present.")
        return

    try:
        result = daemon.try_unlock_stale()
    except QueueLockError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not result.get("removed"):
        console.print("No daemon lock was present.")
        return

    pid = int(result.get("pid") or 0)
    alive = bool(result.get("alive"))
    stale = bool(result.get("stale"))
    if stale:
        age_s = int(float(result.get("age_s") or 0.0))
        console.print(f"Removed stale daemon lock (age_s={age_s}, pid={pid}, alive={alive}).")
    else:
        console.print("Removed orphaned daemon lock (pid not alive).")


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


@artifacts_app.command("prune")
def artifacts_prune(
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        min=1,
        help="Prune artifacts older than this many days.",
    ),
    max_count: int | None = typer.Option(
        None,
        "--max-count",
        min=1,
        help="Keep newest N artifacts; prune the rest.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Perform deletion. Without this flag, only a dry-run preview is shown.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing the artifacts/ subdirectory.",
    ),
) -> None:
    """Prune job artifacts. Dry-run by default; use --yes to delete.

    Scans notes/queue/artifacts/ (or <queue-dir>/artifacts/) for stale entries.
    Selection policy: union — an artifact is pruned if it exceeds *either*
    --max-age-days OR is outside the newest --max-count entries.

    CLI flags override values from ~/.config/voxera/config.json:
      artifacts_retention_days, artifacts_retention_max_count.

    If neither flags nor config is set, prints a message and exits 0 (safe default).
    """
    cfg = load_runtime_config()

    # CLI flags take precedence over config
    effective_age_days = max_age_days if max_age_days is not None else cfg.artifacts_retention_days
    effective_max_count = max_count if max_count is not None else cfg.artifacts_retention_max_count

    artifacts_root = Path(queue_dir).expanduser().resolve() / "artifacts"
    max_age_s = float(effective_age_days) * 86400.0 if effective_age_days is not None else None

    result = prune_artifacts(
        artifacts_root,
        max_age_s=max_age_s,
        max_count=effective_max_count,
        dry_run=not yes,
    )

    if json_out:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    status = result["status"]

    if status == "no_artifacts_dir":
        console.print(f"No artifacts directory at {artifacts_root} — nothing to prune.")
        return

    if status == "no_rules":
        console.print(
            "No pruning rules configured. Set --max-age-days or --max-count, "
            "or add artifacts_retention_days / artifacts_retention_max_count to "
            "~/.config/voxera/config.json."
        )
        return

    dry_run: bool = result["dry_run"]
    total: int = result["total_candidates"]
    pruned: int = result["pruned_count"]
    reclaimed: int = result["reclaimed_bytes"]

    prefix = "[dim](dry-run)[/dim] " if dry_run else ""
    action = "Would prune" if dry_run else "Pruned"

    console.print(f"{prefix}Artifacts root: {artifacts_root}")
    console.print(f"{prefix}Total candidates: {total}")
    console.print(f"{prefix}{action}: {pruned}")
    console.print(f"{prefix}Reclaimed: {format_bytes(reclaimed)}")

    top: list[dict[str, Any]] = result.get("top_entries", [])
    if top:
        table = Table(title="Top Artifacts by Size")
        table.add_column("Name")
        table.add_column("Size", justify="right")
        for entry in top:
            table.add_row(entry["name"], format_bytes(entry["bytes"]))
        console.print(table)

    if dry_run and pruned > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")


@queue_app.command("prune")
def queue_prune(
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        min=1,
        help="Prune jobs older than this many days (terminal buckets only).",
    ),
    max_count: int | None = typer.Option(
        None,
        "--max-count",
        min=1,
        help="Keep newest N jobs per bucket; prune the rest.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Perform deletion. Without this flag, only a dry-run preview is shown.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue root directory containing done/, failed/, canceled/ subdirectories.",
    ),
) -> None:
    """Prune terminal queue buckets. Dry-run by default; use --yes to delete.

    Scans done/, failed/, and canceled/ under the queue root for stale job
    files (job-*.json).  inbox/ and pending/ are never touched.

    Selection policy: union — a job is pruned if it exceeds *either*
    --max-age-days OR is outside the newest --max-count entries per bucket.

    CLI flags override values from ~/.config/voxera/config.json:
      queue_prune_max_age_days, queue_prune_max_count.

    If neither flags nor config is set, prints a message and exits 0 (safe default).
    """
    cfg = load_runtime_config()

    # CLI flags take precedence over config
    effective_age_days = max_age_days if max_age_days is not None else cfg.queue_prune_max_age_days
    effective_max_count = max_count if max_count is not None else cfg.queue_prune_max_count

    queue_root_path = Path(queue_dir).expanduser().resolve()

    result = prune_queue_buckets(
        queue_root_path,
        buckets=TERMINAL_BUCKETS,
        max_age_days=effective_age_days,
        max_count=effective_max_count,
        dry_run=not yes,
    )

    if json_out:
        # Include human-readable status field
        output: dict[str, Any] = {
            "status": "dry_run" if result["dry_run"] else "deleted",
            "queue_dir": result["queue_dir"],
            "buckets_processed": list(TERMINAL_BUCKETS),
            "per_bucket": result["per_bucket"],
            "reclaimed_bytes": result["reclaimed_bytes"],
            "errors": result["errors"],
        }
        if result["status"] == "no_rules":
            output["status"] = "no_rules"
        typer.echo(json.dumps(output, indent=2, sort_keys=True))
        return

    if result["status"] == "no_rules":
        console.print(
            "No pruning rules configured. Set --max-age-days or --max-count, "
            "or add queue_prune_max_age_days / queue_prune_max_count to "
            "~/.config/voxera/config.json."
        )
        return

    dry_run: bool = result["dry_run"]
    prefix = "[dim](dry-run)[/dim] " if dry_run else ""
    action = "Would prune" if dry_run else "Pruned"
    total_selected = 0
    total_reclaimed: int = result["reclaimed_bytes"]

    console.print(f"{prefix}Queue root: {queue_root_path}")
    console.print(f"{prefix}Buckets: {', '.join(TERMINAL_BUCKETS)}")

    per_bucket: dict[str, dict[str, int]] = result["per_bucket"]
    for bucket in TERMINAL_BUCKETS:
        counts = per_bucket.get(bucket, {"candidates": 0, "selected": 0, "pruned": 0})
        candidates = counts["candidates"]
        selected = counts["selected"]
        pruned = counts["pruned"]
        total_selected += pruned
        console.print(
            f"{prefix}  {bucket}/: {candidates} candidates, "
            f"{selected} selected, {pruned} {action.lower()}"
        )

    console.print(f"{prefix}Total {action.lower()}: {total_selected}")
    console.print(f"{prefix}Reclaimed: {format_bytes(total_reclaimed)}")

    errors: list[str] = result.get("errors", [])
    for err in errors:
        console.print(f"[red]Warning:[/red] {err}")

    if dry_run and total_selected > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")
