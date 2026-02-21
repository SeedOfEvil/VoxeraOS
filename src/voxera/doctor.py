from __future__ import annotations

import asyncio
import json
import shutil

from rich.console import Console
from rich.table import Table

from .audit import log as audit_log
from .brain.gemini import GeminiBrain
from .brain.openai_compat import OpenAICompatBrain
from .config import capabilities_report_path, load_config

console = Console()


def _normalize_doctor_result(brain_name: str, provider: str, model: str, result: dict) -> dict:
    json_ok = bool(result.get("json_ok", False))
    note = result.get("note") or result.get("error") or ""
    if not json_ok and not note:
        note = "capability_test returned json_ok=false (no additional info)"

    normalized = {
        "brain": brain_name,
        "provider": str(result.get("provider") or provider),
        "model": str(result.get("model") or model),
        "json_ok": json_ok,
        "latency_s": result.get("latency_s", ""),
        "note": str(note),
    }

    if "error" in result and result.get("error"):
        normalized["error"] = str(result["error"])

    return normalized


async def run_doctor() -> dict:
    cfg = load_config()
    results = {}
    for name, bc in cfg.brain.items():
        try:
            if bc.type == "openai_compat":
                brain = OpenAICompatBrain(
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
                raw_result = {"provider": bc.type, "error": "Unknown provider type", "json_ok": False}
        except Exception as e:
            raw_result = {"provider": bc.type, "error": repr(e), "json_ok": False}

        normalized = _normalize_doctor_result(name, bc.type, bc.model, raw_result)
        results[name] = normalized
        audit_log(
            {
                "event": "doctor_brain_test",
                "brain": normalized["brain"],
                "provider": normalized["provider"],
                "model": normalized["model"],
                "json_ok": normalized["json_ok"],
                "latency_ms": int(float(normalized["latency_s"] or 0) * 1000),
                "note": normalized.get("note", ""),
            }
        )

    results["sandbox.podman"] = {
        "brain": "sandbox.podman",
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


def doctor_sync():
    results = asyncio.run(run_doctor())
    print_report(results)
