"""Panel hygiene + queue mutation bridge.

This module owns the narrow seam that turns panel mutation intents — create
a queue job, submit a panel mission prompt, run a queue-hygiene CLI command,
record a hygiene result into the health snapshot — into actual queue/job
writes and CLI subprocess invocations. It was extracted from
``panel/app.py`` as the second small, behavior-preserving step of
decomposing that composition root (PR B).

``panel/app.py`` remains the composition root: it still defines the FastAPI
app, registers routes, and owns the shared ``_queue_root`` / ``_now_ms``
wrappers that other panel clusters also depend on. This module is explicit
and pure-ish: every function takes the queue root (and any "now" clock) as
an explicit argument, so tests that ``monkeypatch`` panel.app's
``Path.home`` / ``subprocess.run`` / ``sys.executable`` / ``_now_ms`` keep
driving the hygiene / queue mutation flow exactly as before.

Fail-closed semantics:

* ``run_queue_hygiene_command`` never raises for CLI failures — it always
  returns a well-formed result dict with ``ok=False`` and populated
  ``error`` / ``stderr_tail`` / ``exit_code`` fields, and emits a
  ``panel_hygiene_command_failed`` audit event on every failure path. This
  is what the ``/hygiene/prune-dry-run`` and ``/hygiene/reconcile`` routes
  rely on to surface JSON error details to operators without leaking 500s.
* ``write_queue_job`` and ``write_panel_mission_job`` write atomically via
  ``tmp_path.replace(final_path)`` through the standard
  ``enrich_queue_job_payload`` intent lane so the queue-truth envelope is
  preserved exactly (``source_lane=panel_queue_create`` and
  ``panel_mission_prompt`` respectively).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..audit import log
from ..core.queue_job_intent import enrich_queue_job_payload
from ..health import update_health_snapshot

__all__ = [
    "run_queue_hygiene_command",
    "write_panel_mission_job",
    "write_queue_job",
    "write_hygiene_result",
]


def _trim_tail(value: str, *, max_chars: int = 2000) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _repo_root_for_panel_subprocess() -> Path:
    env_root = os.getenv("VOXERA_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate

    default_root = Path(__file__).resolve().parents[3]
    if default_root.exists() and default_root.is_dir():
        return default_root
    return Path.cwd()


def write_queue_job(queue_root: Path, payload: dict[str, Any]) -> str:
    """Write a panel-submitted queue job atomically into ``inbox/``.

    Returns the final filename (e.g. ``job-<ts>-<hex>.json``). The payload
    is enriched via ``enrich_queue_job_payload`` with
    ``source_lane=panel_queue_create`` to preserve the queue-truth intent
    envelope.
    """

    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    tmp_path = inbox / f".{job_id}.tmp.json"
    final_path = inbox / f"{job_id}.json"
    enriched = enrich_queue_job_payload(payload, source_lane="panel_queue_create")
    tmp_path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name


def write_panel_mission_job(
    queue_root: Path,
    *,
    prompt: str,
    approval_required: bool,
) -> tuple[str, str]:
    """Write a panel-submitted mission-prompt job atomically into ``inbox/``.

    Returns ``(filename, mission_id)``. The payload is enriched with
    ``source_lane=panel_mission_prompt`` so the planner / execution lanes
    see a consistent queue-truth envelope.
    """

    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    normalized_prompt = prompt.strip()
    slug = re.sub(r"[^a-z0-9_-]+", "-", normalized_prompt.lower()).strip("-")
    slug = slug[:32] or "mission"
    ts = int(time.time())
    suffix = hashlib.sha1(normalized_prompt.encode("utf-8")).hexdigest()[:6]
    mission_id = re.sub(r"[^a-z0-9_-]+", "-", f"{slug}-{suffix}-{ts}").strip("-")

    payload = enrich_queue_job_payload(
        {
            "id": mission_id,
            "goal": normalized_prompt,
            "approval_required": approval_required,
            "summary": "Panel mission prompt queued for planner",
            "approval_hints": ["manual" if approval_required else "none"],
            "expected_artifacts": [
                "plan.json",
                "execution_envelope.json",
                "execution_result.json",
                "step_results.json",
            ],
        },
        source_lane="panel_mission_prompt",
    )

    base_name = f"job-panel-mission-{slug}-{ts}"
    final_path = inbox / f"{base_name}.json"
    counter = 1
    while final_path.exists():
        final_path = inbox / f"{base_name}-{counter}.json"
        counter += 1

    tmp_path = inbox / f".{final_path.stem}.tmp.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name, mission_id


def run_queue_hygiene_command(queue_root: Path, args: list[str]) -> dict[str, Any]:
    """Invoke ``voxera`` CLI hygiene commands and return a structured result.

    Fail-closed: this function NEVER raises for CLI failures. It always
    returns a dict with at least ``ok`` (bool), ``result`` (dict),
    ``exit_code``, ``stderr_tail``, ``stdout_tail``, ``cmd``, ``cwd``,
    ``attempted`` (list), and ``error`` (str) keys, and emits a
    ``panel_hygiene_command_failed`` audit event on every failure path.
    """

    run_cwd = _repo_root_for_panel_subprocess()
    commands = [
        [sys.executable, "-m", "voxera.cli", *args, "--queue-dir", str(queue_root)],
        ["voxera", *args, "--queue-dir", str(queue_root)],
    ]
    attempted: list[dict[str, Any]] = []

    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=run_cwd)
        except FileNotFoundError as exc:
            attempted.append(
                {
                    "cmd": cmd,
                    "cwd": str(run_cwd),
                    "exit_code": None,
                    "stderr_tail": _trim_tail(str(exc)),
                    "stdout_tail": "",
                }
            )
            continue

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        stdout_tail = _trim_tail(stdout)
        stderr_tail = _trim_tail(stderr)

        result: dict[str, Any] = {
            "ok": False,
            "result": {},
            "exit_code": int(proc.returncode),
            "stderr_tail": stderr_tail,
            "stdout_tail": stdout_tail,
            "cmd": cmd,
            "cwd": str(run_cwd),
            "attempted": attempted,
            "error": "",
        }

        if proc.returncode != 0:
            result["error"] = _trim_tail(stderr or stdout or "command failed")
        else:
            if not stdout.strip():
                result["error"] = "no json output"
            else:
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    result["error"] = "json parse failed"
                else:
                    if not isinstance(parsed, dict):
                        result["error"] = "json parse failed"
                    else:
                        result["ok"] = True
                        result["result"] = parsed

        if not result["ok"]:
            log(
                {
                    "event": "panel_hygiene_command_failed",
                    "cmd": cmd,
                    "rc": int(proc.returncode),
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "error": result["error"],
                    "cwd": str(run_cwd),
                }
            )
        return result

    last_attempt = attempted[-1] if attempted else {}
    error_tail = _trim_tail(
        str(last_attempt.get("stderr_tail") or "voxera CLI executable not found")
    )
    failure = {
        "ok": False,
        "result": {},
        "exit_code": None,
        "stderr_tail": error_tail,
        "stdout_tail": "",
        "cmd": last_attempt.get("cmd", commands[0]),
        "cwd": str(run_cwd),
        "attempted": attempted,
        "error": error_tail,
    }
    log(
        {
            "event": "panel_hygiene_command_failed",
            "cmd": failure["cmd"],
            "rc": None,
            "stderr_tail": error_tail,
            "stdout_tail": "",
            "error": failure["error"],
            "cwd": str(run_cwd),
        }
    )
    return failure


def write_hygiene_result(
    queue_root: Path,
    key: str,
    result: dict[str, Any],
    *,
    now_ms: Callable[[], int],
) -> None:
    """Persist a hygiene run result into the health snapshot under ``key``.

    The ``now_ms`` callable is looked up at apply time (inside the
    ``update_health_snapshot`` closure) so tests that ``monkeypatch``
    ``panel.app._now_ms`` still drive the ``updated_at_ms`` stamp.
    """

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload[key] = result
        payload["updated_at_ms"] = now_ms()
        return payload

    update_health_snapshot(queue_root, _apply)
