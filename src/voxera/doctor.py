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
from .brain.gemini import GeminiBrain
from .brain.openai_compat import OpenAICompatBrain
from .config import capabilities_report_path, load_config
from .core.queue_daemon import MissionQueueDaemon

console = Console()


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


def doctor_sync(*, self_test: bool = False, timeout_s: float = 8.0):
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
