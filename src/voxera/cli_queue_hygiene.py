from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from .cli_common import console, now_ms, queue_dir_path
from .config import load_config as load_runtime_config
from .core.artifacts import format_bytes, prune_artifacts
from .core.queue_hygiene import TERMINAL_BUCKETS, prune_queue_buckets
from .core.queue_reconcile import (
    _default_quarantine_dir,
    quarantine_reconcile_fixes,
    reconcile_queue,
)
from .paths import queue_root_display


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
    effective_age_days = max_age_days if max_age_days is not None else cfg.artifacts_retention_days
    effective_max_count = max_count if max_count is not None else cfg.artifacts_retention_max_count

    artifacts_root = queue_dir_path(queue_dir) / "artifacts"
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
        from rich.table import Table

        table = Table(title="Top Artifacts by Size")
        table.add_column("Name")
        table.add_column("Size", justify="right")
        for entry in top:
            table.add_row(entry["name"], format_bytes(entry["bytes"]))
        console.print(table)

    if dry_run and pruned > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")


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
    """Prune terminal queue buckets. Dry-run by default; use --yes to delete."""
    cfg = load_runtime_config()
    effective_age_days = max_age_days if max_age_days is not None else cfg.queue_prune_max_age_days
    effective_max_count = max_count if max_count is not None else cfg.queue_prune_max_count

    queue_root_path = queue_dir_path(queue_dir)
    result = prune_queue_buckets(
        queue_root_path,
        buckets=TERMINAL_BUCKETS,
        max_age_days=effective_age_days,
        max_count=effective_max_count,
        dry_run=not yes,
    )

    if json_out:
        per_bucket_json: dict[str, dict[str, int]] = result["per_bucket"]
        removed_jobs = int(
            sum(int((per_bucket_json.get(b) or {}).get("pruned", 0) or 0) for b in per_bucket_json)
        )
        output: dict[str, Any] = {
            "status": "dry_run" if result["dry_run"] else "deleted",
            "queue_dir": result["queue_dir"],
            "buckets_processed": list(TERMINAL_BUCKETS),
            "per_bucket": per_bucket_json,
            "by_bucket": per_bucket_json,
            "removed_jobs": 0 if result["dry_run"] else removed_jobs,
            "would_remove_jobs": removed_jobs if result["dry_run"] else 0,
            "removed_sidecars": 0,
            "reclaimed_bytes": result["reclaimed_bytes"],
            "errors": result["errors"],
            "ts_ms": now_ms(),
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
            f"{prefix}  {bucket}/: {candidates} candidates, {selected} selected, {pruned} {action.lower()}"
        )

    console.print(f"{prefix}Total {action.lower()}: {total_selected}")
    console.print(f"{prefix}Reclaimed: {format_bytes(total_reclaimed)}")

    errors: list[str] = result.get("errors", [])
    for err in errors:
        console.print(f"[red]Warning:[/red] {err}")

    if dry_run and total_selected > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")


def queue_reconcile(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON report.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue root directory to scan for hygiene issues.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Enable fix mode: quarantine safe orphan files. Without --yes, runs as a dry-run preview.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Actually perform quarantine moves. Required with --fix to apply changes.",
    ),
    quarantine_dir: str = typer.Option(
        "",
        "--quarantine-dir",
        help=(
            "Directory to quarantine orphan files into. "
            "Must be within --queue-dir. "
            "Default: <queue-dir>/quarantine/reconcile-YYYYMMDD-HHMMSS/"
        ),
    ),
) -> None:
    """Scan queue directory and report hygiene issues. Report-only by default; no changes made."""
    queue_root_path = queue_dir_path(queue_dir)
    report = reconcile_queue(queue_root_path)

    q_dir: Path | None = None
    if fix:
        if quarantine_dir:
            q_dir = Path(quarantine_dir).expanduser().resolve()
        else:
            q_dir = _default_quarantine_dir(queue_root_path)
        try:
            q_dir.relative_to(queue_root_path)
        except ValueError as exc:
            typer.echo(
                f"Error: --quarantine-dir must be within --queue-dir ({queue_root_path}).",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    dry_run = not yes
    mode = "report" if not fix else ("fix_preview" if dry_run else "fix_applied")

    fix_results: dict[str, Any] = {}
    if fix and q_dir is not None:
        fix_results = quarantine_reconcile_fixes(queue_root_path, q_dir, dry_run=dry_run)

    if json_out:
        out: dict[str, Any] = {
            "status": "ok",
            "queue_dir": str(queue_root_path),
            "mode": mode,
            "quarantine_dir": str(q_dir) if q_dir is not None else None,
            "issue_counts": report["issue_counts"],
            "examples": report["examples"],
            "ts_ms": now_ms(),
        }
        if fix_results:
            out["fix_counts"] = {
                "orphan_sidecars_quarantined": fix_results["orphan_sidecars_quarantined"],
                "orphan_sidecars_would_quarantine": fix_results["orphan_sidecars_would_quarantine"],
                "orphan_approvals_quarantined": fix_results["orphan_approvals_quarantined"],
                "orphan_approvals_would_quarantine": fix_results[
                    "orphan_approvals_would_quarantine"
                ],
            }
            out["quarantined_paths"] = fix_results["quarantined_paths"]
        else:
            out["fix_counts"] = {
                "orphan_sidecars_quarantined": 0,
                "orphan_sidecars_would_quarantine": 0,
                "orphan_approvals_quarantined": 0,
                "orphan_approvals_would_quarantine": 0,
            }
            out["quarantined_paths"] = []
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
        return

    counts = report["issue_counts"]
    examples = report["examples"]

    console.print(f"Queue root: {queue_root_path}")
    if fix:
        console.print(f"Quarantine dir: {q_dir}")
        if dry_run:
            console.print("[yellow]Mode: fix preview (dry-run — no changes made)[/yellow]")
        else:
            console.print("[cyan]Mode: fix applied[/cyan]")
    else:
        console.print("[dim]Mode: report-only[/dim]")
    console.print()

    total = sum(counts.values())
    if total == 0:
        console.print("[green]Queue looks clean — no hygiene issues detected.[/green]")
    else:
        console.print(f"[yellow]Issues found:[/yellow] {total} total")
    console.print()

    path_issue_labels: list[tuple[str, str]] = [
        ("orphan_sidecars", "Orphan sidecars (terminal buckets)"),
        ("orphan_approvals", "Orphan approvals (pending/approvals/)"),
        ("orphan_artifacts_candidate", "Orphan artifact candidates (artifacts/) [report-only]"),
    ]
    for key, label in path_issue_labels:
        count = counts[key]
        console.print(f"  {label}: {count}")
        for path in examples[key]:
            console.print(f"    {path}")

    dup_count = counts["duplicate_jobs"]
    console.print(f"  Duplicate job filenames across buckets: {dup_count} [report-only]")
    for entry in examples["duplicate_jobs"]:
        buckets_str = ", ".join(entry["buckets"])
        console.print(f"    {entry['job_name']} — buckets: {buckets_str}")

    if fix and fix_results:
        console.print()
        if dry_run:
            console.print(
                f"  Would quarantine orphan sidecars: {fix_results['orphan_sidecars_would_quarantine']}"
            )
            console.print(
                f"  Would quarantine orphan approvals: {fix_results['orphan_approvals_would_quarantine']}"
            )
            paths = fix_results["quarantined_paths"]
            if paths:
                console.print("  Preview (up to 10 paths that would move):")
                for p in paths:
                    console.print(f"    {p}")
            console.print()
            console.print("[yellow]Hint:[/yellow] Run with --fix --yes to apply quarantine.")
        else:
            console.print(
                f"  Quarantined orphan sidecars: {fix_results['orphan_sidecars_quarantined']}"
            )
            console.print(
                f"  Quarantined orphan approvals: {fix_results['orphan_approvals_quarantined']}"
            )
            paths = fix_results["quarantined_paths"]
            if paths:
                console.print("  Quarantined paths (up to 10):")
                for p in paths:
                    console.print(f"    {p}")
            if fix_results.get("errors"):
                for err in fix_results["errors"]:
                    console.print(f"  [red]Warning:[/red] {err}")

    console.print()
    if fix and not dry_run:
        console.print(
            "[dim]No deletions performed; quarantined files can be restored manually.[/dim]"
        )
    else:
        console.print("[dim]Report-only; no changes made.[/dim]")
