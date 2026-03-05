from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from . import audit
from .audit import log
from .brain.gemini import GeminiBrain
from .brain.openai_compat import OpenAICompatBrain
from .config import capabilities_report_path
from .config import load_app_config as load_config
from .core.queue_daemon import MissionQueueDaemon
from .health import read_health_snapshot
from .health_semantics import build_health_semantic_sections

console = Console()
_STALE_LAST_ERROR_THRESHOLD_MS = 5 * 60 * 1000


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_duration(ms: int) -> str:
    seconds = max(0, ms // 1000)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def run_self_test(*, timeout_s: float = 8.0) -> dict[str, Any]:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="voxera-doctor-") as tmp:
        queue_root = Path(tmp) / "queue"
        queue_root.mkdir(parents=True, exist_ok=True)
        daemon = MissionQueueDaemon(queue_root=queue_root)
        daemon.ensure_dirs()

        job = queue_root / "inbox" / "doctor-self-test.json"
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text(json.dumps({"mission_id": "system_check"}, indent=2), encoding="utf-8")

        done_job = queue_root / "done" / job.name
        failed_job = queue_root / "failed" / job.name
        while time.time() - started < timeout_s:
            daemon.process_pending_once()
            if done_job.exists() or failed_job.exists():
                break
            time.sleep(0.15)

        audit_events = [
            e
            for e in audit.tail(200)
            if str(e.get("job", "")).endswith(job.name)
            or e.get("event") in {"queue_job_started", "queue_job_done", "queue_job_failed"}
        ]
        artifacts_dir = queue_root / "artifacts" / job.stem
        required = [
            artifacts_dir / "actions.jsonl",
            artifacts_dir / "plan.json",
            artifacts_dir / "stdout.txt",
            artifacts_dir / "stderr.txt",
        ]
        missing = [str(item) for item in required if not item.exists()]

        ok = done_job.exists() and bool(audit_events) and not missing
        fixes: list[str] = []
        if not done_job.exists() and not failed_job.exists():
            fixes.append(
                "Daemon did not complete job in time; verify queue daemon processing and mission load."
            )
        if not audit_events:
            fixes.append(
                "No audit events correlated with self-test job; verify audit path permissions."
            )
        if missing:
            fixes.append("Missing artifact files; verify artifact writer hooks in queue daemon.")

        return {
            "ok": ok,
            "queue_root": str(queue_root),
            "job": job.name,
            "done": done_job.exists(),
            "failed": failed_job.exists(),
            "audit_events": len(audit_events),
            "artifacts_dir": str(artifacts_dir),
            "missing_artifacts": missing,
            "fixes": fixes,
            "duration_s": round(time.time() - started, 3),
        }


def _normalize_brain_result(
    name: str, provider: str, model: str, result: dict[str, Any]
) -> dict[str, Any]:
    normalized = dict(result)
    normalized["provider"] = str(normalized.get("provider") or provider)
    normalized["model"] = str(normalized.get("model") or model)
    json_ok = bool(normalized.get("json_ok", False))
    normalized["json_ok"] = json_ok
    if not json_ok and not str(normalized.get("note") or "").strip():
        normalized["note"] = "invalid_json: capability_test returned json_ok=false (no details)"

    latency_s = normalized.get("latency_s")
    try:
        latency_ms = int(float(latency_s) * 1000) if latency_s is not None else None
    except (TypeError, ValueError):
        latency_ms = None

    with contextlib.suppress(OSError):
        audit.log(
            {
                "event": "doctor_brain_test",
                "brain": name,
                "provider": normalized.get("provider"),
                "model": normalized.get("model"),
                "json_ok": json_ok,
                "latency_ms": latency_ms,
                "note": normalized.get("note") or normalized.get("error") or "",
            }
        )
    return normalized


async def run_doctor() -> dict:
    cfg = load_config()
    results = {}
    for name, bc in cfg.brain.items():
        try:
            if bc.type == "openai_compat":
                brain: OpenAICompatBrain | GeminiBrain = OpenAICompatBrain(
                    base_url=bc.base_url or "",
                    model=bc.model,
                    api_key_ref=bc.api_key_ref,
                    extra_headers=bc.extra_headers,
                )
                raw_result = await brain.capability_test()
            elif bc.type == "gemini":
                brain = GeminiBrain(model=bc.model, api_key_ref=bc.api_key_ref)
                raw_result = await brain.capability_test()
            else:
                raw_result = {
                    "provider": bc.type,
                    "model": bc.model,
                    "error": "Unknown provider type",
                }
        except Exception as e:
            raw_result = {"provider": bc.type, "model": bc.model, "error": repr(e)}

        results[name] = _normalize_brain_result(name, bc.type, bc.model, raw_result)

    results["sandbox.podman"] = {
        "provider": "podman",
        "model": cfg.sandbox_image,
        "json_ok": shutil.which("podman") is not None,
        "latency_s": "",
        "note": "rootless podman available"
        if shutil.which("podman")
        else "podman missing: install rootless podman for sandbox skills",
    }
    capabilities_report_path().write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def print_report(results: dict) -> None:
    if not results:
        console.print("[yellow]No brain providers configured. Run 'voxera setup' first.[/yellow]")
        return

    t = Table(title="Voxera Doctor Report")
    t.add_column("Brain")
    t.add_column("Provider")
    t.add_column("Model")
    t.add_column("JSON OK")
    t.add_column("Latency (s)")
    t.add_column("Note/Error")
    for name, r in results.items():
        t.add_row(
            name,
            str(r.get("provider", "")),
            str(r.get("model", "")),
            str(r.get("json_ok", "")),
            str(r.get("latency_s", "")),
            str(r.get("note") or r.get("error") or ""),
        )
    console.print(t)


def doctor_sync(*, self_test: bool = False, timeout_s: float = 8.0, quick: bool = False):
    if quick:
        try:
            checks = run_quick_doctor()
        except Exception as exc:
            log({"event": "doctor_quick_error", "error": type(exc).__name__})
            raise
        print_quick_report(checks)
        return
    results = asyncio.run(run_doctor())
    if self_test:
        results["self_test"] = run_self_test(timeout_s=timeout_s)
    print_report(results)
    if self_test:
        status = results["self_test"]
        if status["ok"]:
            console.print("[green]Self-test PASS[/green]")
        else:
            console.print("[red]Self-test FAIL[/red]")
            for step in status.get("fixes", []):
                console.print(f"- {step}")


def run_quick_doctor(
    *,
    queue_root: Path | None = None,
    stale_last_error_threshold_ms: int = _STALE_LAST_ERROR_THRESHOLD_MS,
) -> list[dict[str, str]]:
    root = (queue_root or (Path.home() / "VoxeraOS" / "notes" / "queue")).expanduser()
    daemon = MissionQueueDaemon(queue_root=root)
    checks: list[dict[str, str]] = []

    snap = daemon.status_snapshot()
    lock = snap.get("lock_status", {}) if isinstance(snap.get("lock_status"), dict) else {}
    checks.append(
        {
            "check": "current state: lock status",
            "status": "ok" if bool(lock.get("exists")) and bool(lock.get("alive")) else "warn",
            "detail": f"exists={bool(lock.get('exists'))} pid={lock.get('pid')} alive={bool(lock.get('alive'))}",
            "hint": "Run `voxera queue unlock` if lock is stale."
            if bool(lock.get("exists")) and not bool(lock.get("alive"))
            else "",
        }
    )

    health = read_health_snapshot(root)
    grouped = build_health_semantic_sections(
        health,
        queue_context={
            "queue_root": str(root),
            "health_path": str(root / "health.json"),
            "intake_glob": str(root / "inbox" / "*.json"),
            "paused": bool(snap.get("paused", False)),
        },
        lock_status=lock,
        daemon_lock_counters=snap.get("daemon_lock_counters")
        if isinstance(snap.get("daemon_lock_counters"), dict)
        else {},
    )
    current_state = grouped["current_state"]
    recent_history = grouped["recent_history"]
    historical_counters = grouped["historical_counters"]

    daemon_state = str(current_state.get("daemon_state", "healthy"))
    degradation = (
        current_state.get("degradation", {})
        if isinstance(current_state.get("degradation"), dict)
        else {}
    )
    degraded_reason = degradation.get("degraded_reason") or "-"
    checks.append(
        {
            "check": "current state: runtime health",
            "status": "warn" if daemon_state == "degraded" else "ok",
            "detail": (
                f"daemon_state={daemon_state} failures={degradation.get('consecutive_brain_failures', 0)} "
                f"backoff_active={degradation.get('brain_backoff_active', False)} reason={degraded_reason}"
            ),
            "hint": "Current runtime is degraded; inspect recent history for latest incident context."
            if daemon_state == "degraded"
            else "",
        }
    )

    last_ok_event = str(recent_history.get("last_ok_event") or "").strip()
    checks.append(
        {
            "check": "recent history: last_ok",
            "status": "ok" if bool(last_ok_event) else "warn",
            "detail": (
                f"event={last_ok_event or '-'} ts={recent_history.get('last_ok_ts_ms') if recent_history.get('last_ok_ts_ms') is not None else '-'}"
            ),
            "hint": "No recent successful health event recorded."
            if not bool(last_ok_event)
            else "",
        }
    )

    last_error = str(recent_history.get("last_error") or "").strip()
    last_error_ts_ms = _as_int(recent_history.get("last_error_ts_ms"))
    last_ok_ts_ms = _as_int(recent_history.get("last_ok_ts_ms"))
    error_status = "warn" if bool(last_error) else "ok"
    error_hint = "Investigate latest daemon/panel errors if this persists." if last_error else ""
    error_detail = f"error={last_error or '-'} ts={recent_history.get('last_error_ts_ms') if recent_history.get('last_error_ts_ms') is not None else '-'}"

    if (
        last_error
        and last_error_ts_ms is not None
        and last_ok_ts_ms is not None
        and (last_ok_ts_ms - last_error_ts_ms) > stale_last_error_threshold_ms
    ):
        delta_ms = last_ok_ts_ms - last_error_ts_ms
        error_status = "ok"
        error_hint = ""
        error_detail = (
            f"error={last_error} ts={last_error_ts_ms} "
            f"(stale; ok newer by {_compact_duration(delta_ms)})"
        )

    checks.append(
        {
            "check": "recent history: last_error",
            "status": error_status,
            "detail": error_detail,
            "hint": error_hint,
        }
    )

    fallback = recent_history.get("last_brain_fallback", {})
    fb_reason = str(fallback.get("reason") or "") if isinstance(fallback, dict) else ""
    fb_from = str(fallback.get("from") or "") if isinstance(fallback, dict) else ""
    fb_to = str(fallback.get("to") or "") if isinstance(fallback, dict) else ""
    fb_ts = fallback.get("ts_ms") if isinstance(fallback, dict) else ""
    if fb_reason:
        fb_detail = (
            f"{fb_from or '-'} -> {fb_to or '-'} reason={fb_reason} ts={fb_ts if fb_ts else '-'}"
        )
        fb_hint = (
            "RATE_LIMIT implies API throttling; AUTH implies bad key/config; "
            "TIMEOUT implies network/provider slowness."
            if fb_reason in ("RATE_LIMIT", "AUTH", "TIMEOUT")
            else ""
        )
    else:
        fb_detail = "-"
        fb_hint = ""
    checks.append(
        {
            "check": "recent history: last fallback",
            "status": "ok" if not fb_reason else "warn",
            "detail": fb_detail,
            "hint": fb_hint,
        }
    )

    shutdown_outcome = recent_history.get("last_shutdown_outcome")
    shutdown_detail = (
        "none"
        if not shutdown_outcome
        else "outcome={outcome} ts={ts} reason={reason} job={job}".format(
            outcome=shutdown_outcome,
            ts=recent_history.get("last_shutdown_ts"),
            reason=recent_history.get("last_shutdown_reason") or "-",
            job=recent_history.get("last_shutdown_job") or "-",
        )
    )
    checks.append(
        {
            "check": "recent history: last shutdown",
            "status": "ok",
            "detail": shutdown_detail,
            "hint": "",
        }
    )

    checks.append(
        {
            "check": "historical counters: panel auth",
            "status": "warn"
            if int(current_state.get("panel_auth_lockouts", {}).get("locked_out_ips", 0) or 0) > 0
            else "ok",
            "detail": (
                f"locked_out_ips={current_state.get('panel_auth_lockouts', {}).get('locked_out_ips', 0)} "
                f"401={int(historical_counters.get('panel_401_count', 0) or 0)} "
                f"403={int(historical_counters.get('panel_403_count', 0) or 0)} "
                f"429={int(historical_counters.get('panel_429_count', 0) or 0)}"
            ),
            "hint": "Counters are cumulative; spikes indicate repeated authentication or lockout issues.",
        }
    )

    checks.append(
        {
            "check": "queue counts",
            "status": "ok",
            "detail": "inbox={inbox} pending={pending} approvals={approvals} done={done} failed={failed}".format(
                inbox=snap.get("counts", {}).get("inbox", 0),
                pending=snap.get("counts", {}).get("pending", 0),
                approvals=snap.get("counts", {}).get("pending_approvals", 0),
                done=snap.get("counts", {}).get("done", 0),
                failed=snap.get("counts", {}).get("failed", 0),
            ),
            "hint": "",
        }
    )

    log({"event": "doctor_quick_ok", "queue_root": str(root), "checks": len(checks)})
    return checks


def print_quick_report(checks: list[dict[str, str]]) -> None:
    table = Table(title="Voxera Doctor Quick (offline)")
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Detail")
    table.add_column("Hint")
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    for row in checks:
        table.add_row(icon.get(row["status"], "⚠️"), row["check"], row["detail"], row["hint"])
    console.print(table)
