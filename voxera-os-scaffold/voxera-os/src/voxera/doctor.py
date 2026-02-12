from __future__ import annotations

import asyncio
import json
from rich.console import Console
from rich.table import Table
from .config import load_config, capabilities_report_path
from .brain.openai_compat import OpenAICompatBrain
from .brain.gemini import GeminiBrain

console = Console()

async def run_doctor() -> dict:
    cfg = load_config()
    results = {}
    for name, bc in cfg.brain.items():
        try:
            if bc.type == "openai_compat":
                brain = OpenAICompatBrain(base_url=bc.base_url or "", model=bc.model, api_key_ref=bc.api_key_ref)
                results[name] = await brain.capability_test()
            elif bc.type == "gemini":
                brain = GeminiBrain(model=bc.model, api_key_ref=bc.api_key_ref)
                results[name] = await brain.capability_test()
            else:
                results[name] = {"provider": bc.type, "error": "Unknown provider type"}
        except Exception as e:
            results[name] = {"provider": bc.type, "error": repr(e)}
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
