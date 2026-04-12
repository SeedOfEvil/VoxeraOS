from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from . import audit as _audit
from .cli_automation import automation_app
from .cli_common import (
    DEMO_QUEUE_DIR_OPTION,
    OPS_BUNDLE_ARCHIVE_DIR_OPTION,
    RUN_ARG_OPTION,
    SNAPSHOT_PATH_OPTION,
    console,
    require_config,
)
from .cli_config import config_show_impl, config_snapshot_impl, config_validate_impl
from .cli_doctor import register as register_doctor
from .cli_ops import ops_bundle_job_impl, ops_bundle_system_impl, ops_capabilities_impl
from .cli_queue import artifacts_app, inbox_app, queue_app
from .cli_runtime import (
    audit_impl,
    daemon_impl,
    demo_cmd_impl,
    panel_impl,
    setup_impl,
    status_impl,
    vera_impl,
)
from .cli_skills_missions import (
    approval_prompt_impl,
    missions_list_impl,
    missions_plan_impl,
    missions_run_impl,
    run_impl,
    skills_list_impl,
)
from .config import load_app_config as load_config
from .config import load_config as load_runtime_config
from .config import (
    load_runtime_env,
    should_load_dotenv,
    write_config_fingerprint,
    write_config_snapshot,
)
from .core.capabilities_snapshot import generate_capabilities_snapshot
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .demo import run_demo
from .paths import queue_root_display
from .secrets import get_secret, unset_secret, write_secret
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
skills_app = typer.Typer(help="Manage skills")
missions_app = typer.Typer(help="Run multi-step built-in missions")
ops_app = typer.Typer(help="Operational incident bundle utilities")
ops_bundle_app = typer.Typer(help="Export operator bundles")
secrets_app = typer.Typer(help="Manage provider/API secrets")

app.add_typer(config_app, name="config")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(skills_app, name="skills")
app.add_typer(missions_app, name="missions")
app.add_typer(queue_app, name="queue")
app.add_typer(ops_app, name="ops")
app.add_typer(inbox_app, name="inbox")
app.add_typer(secrets_app, name="secrets")
app.add_typer(automation_app, name="automation")
ops_app.add_typer(ops_bundle_app, name="bundle")


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name/ref (for example BRAVE_API_KEY)."),
    value: str | None = typer.Option(
        None,
        "--value",
        help="Secret value. If omitted, prompts securely with hidden input.",
    ),
):
    """Store a secret via keyring (with secure file fallback)."""
    secret_value = value
    if secret_value is None:
        secret_value = typer.prompt("Secret value", hide_input=True, confirmation_prompt=True)
    write_secret(name, secret_value)
    console.print(f"Stored secret: {name}")


@secrets_app.command("get")
def secrets_get(
    name: str = typer.Argument(..., help="Secret name/ref to query."),
    exists_only: bool = typer.Option(
        False,
        "--exists-only",
        help="Only report whether a secret exists (default behavior).",
    ),
    show_value: bool = typer.Option(
        False,
        "--show-value",
        help="Print raw secret value. Use with caution.",
    ),
):
    """Read secret metadata safely (raw value hidden by default)."""
    resolved = get_secret(name)
    exists = resolved is not None and bool(resolved.strip())

    if exists_only or not show_value:
        console.print("present" if exists else "missing")
        if exists_only and not exists:
            raise typer.Exit(code=1)
        return

    if not exists:
        console.print("missing")
        raise typer.Exit(code=1)

    console.print(resolved)


@secrets_app.command("unset")
def secrets_unset(
    name: str = typer.Argument(..., help="Secret name/ref to remove."),
):
    """Remove a stored secret from keyring/file fallback."""
    removed = unset_secret(name)
    if removed:
        console.print(f"Removed secret: {name}")
        return
    console.print(f"Secret not found: {name}")
    raise typer.Exit(code=1)


@config_app.command("show")
def config_show():
    """Show resolved runtime config (redacted)."""
    config_show_impl(load_runtime_config=load_runtime_config)


@app.command("config-show")
def config_show_legacy():
    """Backward-compatible alias for `voxera config show`."""
    config_show_impl(load_runtime_config=load_runtime_config)


@config_app.command("snapshot")
def config_snapshot(path: Path | None = SNAPSHOT_PATH_OPTION) -> None:
    """Write a redacted runtime config snapshot and print its absolute path."""
    config_snapshot_impl(
        load_runtime_config=load_runtime_config,
        write_config_snapshot=write_config_snapshot,
        write_config_fingerprint=write_config_fingerprint,
        path=path,
    )


@config_app.command("validate")
def config_validate():
    """Validate runtime config and exit non-zero on errors."""
    config_validate_impl(load_runtime_config=load_runtime_config)


register_doctor(app)


@app.command()
def setup():
    """Run first-run typed setup wizard."""
    setup_impl(run_setup=run_setup)


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
    demo_cmd_impl(
        run_demo=run_demo, queue_dir=queue_dir, online=online, yes=yes, json_output=json_output
    )


@app.command()
def status():
    """Show current configuration summary."""
    status_impl(load_config=load_config)


@skills_app.command("list")
def skills_list():
    skills_list_impl(skill_registry_cls=SkillRegistry)


def _approval_prompt(manifest, decision):
    return approval_prompt_impl(manifest, decision)


@app.command()
def run(
    skill_id: str,
    arg: list[str] | None = RUN_ARG_OPTION,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate execution without running the skill."
    ),
    allow_direct_mutation: bool = typer.Option(
        False,
        "--allow-direct-mutation",
        help=(
            "DEV ONLY: allow direct CLI execution of mutating skills (requires VOXERA_DEV_MODE=1)."
        ),
    ),
):
    """Run a skill by ID (MVP)."""
    run_impl(
        load_config=load_config,
        skill_registry_cls=SkillRegistry,
        skill_runner_cls=SkillRunner,
        approval_prompt=_approval_prompt,
        skill_id=skill_id,
        arg=arg,
        dry_run=dry_run,
        allow_direct_mutation=allow_direct_mutation,
    )


@missions_app.command("list")
def missions_list():
    missions_list_impl()


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
    missions_plan_impl(
        load_config=load_config,
        skill_registry_cls=SkillRegistry,
        skill_runner_cls=SkillRunner,
        approval_prompt=_approval_prompt,
        goal=goal,
        dry_run=dry_run,
        freeze_capabilities_snapshot=freeze_capabilities_snapshot,
        deterministic=deterministic,
    )


@missions_app.command("run")
def missions_run(
    mission_id: str,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate mission execution without running skills."
    ),
):
    """Run a built-in multi-step mission by ID."""
    missions_run_impl(
        load_config=load_config,
        skill_registry_cls=SkillRegistry,
        skill_runner_cls=SkillRunner,
        approval_prompt=_approval_prompt,
        mission_id=mission_id,
        dry_run=dry_run,
    )


@app.command()
def audit(n: int = 30):
    """Show last N audit events."""
    audit_impl(tail=tail, n=n)


@app.command()
def panel(
    host: str | None = typer.Option(None, "--host", help="Panel host override."),
    port: int | None = typer.Option(None, "--port", help="Panel port override."),
):
    """Run the minimal approvals/audit panel."""
    require_config()
    panel_impl(load_runtime_config=load_runtime_config, host=host, port=port)


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
    require_config()
    daemon_impl(
        MissionQueueDaemon_cls=MissionQueueDaemon,
        queue_lock_error_cls=QueueLockError,
        once=once,
        queue_dir=queue_dir,
        poll_interval=poll_interval,
        auto_approve_ask=auto_approve_ask,
    )


@app.command()
def vera(
    host: str = typer.Option("127.0.0.1", "--host", help="Vera host override."),
    port: int = typer.Option(8790, "--port", help="Vera port override."),
):
    """Run the standalone Vera web app."""
    require_config()
    vera_impl(host=host, port=port)


@ops_app.command("capabilities")
def ops_capabilities():
    """Print runtime capabilities snapshot JSON."""
    ops_capabilities_impl(
        skill_registry_cls=SkillRegistry,
        generate_capabilities_snapshot=generate_capabilities_snapshot,
    )


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
    ops_bundle_system_impl(queue_dir=queue_dir, archive_dir=archive_dir)


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
    ops_bundle_job_impl(job_ref=job_ref, queue_dir=queue_dir, archive_dir=archive_dir)


if __name__ == "__main__":
    app()
