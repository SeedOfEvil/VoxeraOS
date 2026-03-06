from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import typer
from rich.table import Table

from . import audit as _audit
from .cli_common import (
    DEMO_QUEUE_DIR_OPTION,
    OPS_BUNDLE_ARCHIVE_DIR_OPTION,
    RUN_ARG_OPTION,
    SNAPSHOT_PATH_OPTION,
    console,
    queue_dir_path,
)
from .cli_doctor import register as register_doctor
from .cli_queue import artifacts_app, inbox_app, queue_app
from .config import load_app_config as load_config
from .config import load_config as load_runtime_config
from .config import (
    load_runtime_env,
    should_load_dotenv,
    write_config_fingerprint,
    write_config_snapshot,
)
from .core.capabilities_snapshot import (
    generate_capabilities_snapshot,
    validate_mission_id_against_snapshot,
    validate_mission_steps_against_snapshot,
)
from .core.mission_planner import MissionPlannerError, plan_mission
from .core.missions import MissionRunner, _make_dryrun_deterministic, get_mission, list_missions
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .demo import run_demo
from .ops_bundle import build_job_bundle as build_ops_job_bundle
from .ops_bundle import build_system_bundle as build_ops_system_bundle
from .paths import queue_root_display
from .setup_wizard import run_setup
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner
from .version import get_version

log = _audit.log
tail = _audit.tail


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


skills_app = typer.Typer(help="Manage skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
ops_app = typer.Typer(help="Operational incident bundle utilities")
ops_bundle_app = typer.Typer(help="Export operator bundles")

app.add_typer(artifacts_app, name="artifacts")
app.add_typer(skills_app, name="skills")
app.add_typer(missions_app, name="missions")
app.add_typer(queue_app, name="queue")
app.add_typer(ops_app, name="ops")
app.add_typer(inbox_app, name="inbox")
ops_app.add_typer(ops_bundle_app, name="bundle")
register_doctor(app)


@app.command()
def setup():
    """Run first-run typed setup wizard."""
    asyncio.run(run_setup())


@app.command("demo")
def demo_cmd(
    queue_dir: Path | None = DEMO_QUEUE_DIR_OPTION,
    online: bool = typer.Option(
        False, "--online", help="Opt in to online/provider readiness checks."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Perform optional actions (demo-only cleanup). Without this, optional actions are preview-only.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
):
    """Run a safe 5-minute guided demo checklist."""
    result = run_demo(queue_dir=queue_dir, online=online, yes=yes)
    if json_output:
        typer.echo(json.dumps(result, sort_keys=True))
        return

    table = Table(title="Voxera Demo Checklist")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in result["checks"]:
        table.add_row(str(check["name"]), str(check["status"]), str(check["detail"]))
    console.print(table)

    if result["created_jobs"]:
        console.print("Created demo jobs:")
        for job_name in result["created_jobs"]:
            console.print(f"- {job_name}")

    cleanup = result["cleanup"]
    if cleanup["performed"]:
        console.print(f"Optional cleanup removed {cleanup['removed']} demo-scoped item(s).")
    else:
        console.print(
            "Optional cleanup skipped (run with --yes to remove demo-* items created for demos)."
        )

    console.print(f"Overall demo status: {result['status']}")


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
        queue_root=queue_dir_path(queue_dir),
        poll_interval=poll_interval,
        auto_approve_ask=auto_approve_ask,
    )
    try:
        daemon.run(once=once)
    except QueueLockError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        console.print("Queue daemon stopped.")


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
    queue_root = queue_dir_path(queue_dir)
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
    queue_root = queue_dir_path(queue_dir)
    out = build_ops_job_bundle(
        queue_root,
        job_ref,
        archive_dir=archive_dir,
        prefer_queue_root_archive=True,
    )
    typer.echo(str(out.resolve()))


if __name__ == "__main__":
    app()
