from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..audit import log, tail
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config as load_config
from ..config import load_config as load_runtime_config
from ..config import write_config_fingerprint, write_config_snapshot
from ..health import (
    compute_brain_backoff_s,
    increment_health_counter,
    read_health_snapshot,
    record_brain_backoff_applied,
    record_health_error,
    record_health_ok,
    record_last_shutdown,
    record_mission_success,
    update_health_snapshot,
)
from ..operator_assistant import (
    ASSISTANT_JOB_KIND,
    append_thread_turn,
    build_assistant_messages,
    build_operator_assistant_context,
    fallback_operator_answer,
    normalize_thread_id,
    read_assistant_thread,
)
from ..paths import queue_root as default_queue_root
from ..skills.registry import SkillRegistry
from ..skills.runner import SkillRunner
from .capabilities_snapshot import (
    generate_capabilities_snapshot,
    validate_mission_id_against_snapshot,
    validate_mission_steps_against_snapshot,
)
from .mission_planner import MissionPlannerError, plan_mission
from .missions import MissionRunner, MissionStep, MissionTemplate, get_mission

_AUTO_APPROVE_ALLOWLIST = {"system.settings"}
_PARSE_RETRY_ATTEMPTS = 4
_PARSE_RETRY_BACKOFF_S = 0.1
_FAILED_SIDECAR_SCHEMA_WRITE_VERSION = 1
_FAILED_SIDECAR_SCHEMA_READ_VERSIONS = {_FAILED_SIDECAR_SCHEMA_WRITE_VERSION}
_FAILED_TIMESTAMP_MS_MIN = 10**12
_APPROVAL_GRANTS_FILE = "grants.json"
_STARTUP_RECOVERY_REASON = "recovered_after_restart"
_STARTUP_RECOVERY_MESSAGE = (
    "daemon recovered from unclean shutdown; job marked failed deterministically"
)
_SHUTDOWN_REASON_MAX_LEN = 240
_JOB_STATE_SCHEMA_VERSION = 1
_ASSISTANT_ADVISORY_MODE_QUEUE = "queue"
_ASSISTANT_ADVISORY_MODE_DEGRADED = "degraded_brain_only"


class QueueLockError(RuntimeError):
    pass


@dataclass
class QueueStats:
    processed: int = 0
    failed: int = 0


class MissionQueueDaemon:
    def __init__(
        self,
        queue_root: Path | None = None,
        poll_interval: float = 1.0,
        mission_log_path: Path | None = None,
        *,
        auto_approve_ask: bool = False,
        failed_retention_max_age_s: float | None = None,
        failed_retention_max_count: int | None = None,
    ):
        self.queue_root = (queue_root or default_queue_root()).expanduser()
        self.inbox = self.queue_root / "inbox"
        self.done = self.queue_root / "done"
        self.failed = self.queue_root / "failed"
        self.canceled = self.queue_root / "canceled"
        self.pending = self.queue_root / "pending"
        self.approvals = self.pending / "approvals"
        self.artifacts = self.queue_root / "artifacts"
        self.archive = self.queue_root / "_archive"
        self.pause_marker = self.queue_root / ".paused"
        self.lock_file = self.queue_root / ".daemon.lock"
        self.settings = load_runtime_config()
        self.lock_stale_after_s = self.settings.queue_lock_stale_s or 3600.0
        self._lock_held = False
        self._lock_fd: int | None = None
        self.poll_interval = poll_interval
        self.stats = QueueStats()
        self.current_job_ref: str | None = None
        self._shutdown_requested = False
        self._shutdown_reason: str | None = None
        self._approved_steps: set[tuple[str, int, str]] = set()

        cfg = load_config()
        reg = SkillRegistry()
        reg.discover()
        runner = SkillRunner(reg)
        runner.config = cfg
        self.mission_runner = MissionRunner(
            runner,
            policy=cfg.policy,
            require_approval_cb=self._queue_approval_prompt,
            redact_logs=cfg.privacy.redact_logs,
            mission_log_path=mission_log_path,
        )
        self.cfg = cfg
        self.auto_approve_ask = auto_approve_ask
        self.dev_mode = self.settings.dev_mode
        self.failed_retention_max_age_s = (
            failed_retention_max_age_s
            if failed_retention_max_age_s is not None
            else self.settings.queue_failed_max_age_s
        )
        self.failed_retention_max_count = (
            failed_retention_max_count
            if failed_retention_max_count is not None
            else self.settings.queue_failed_max_count
        )

    def _decision_capability(self, decision) -> str:
        first = (decision.reason or "").split(";", 1)[0].strip()
        return first.split(" ->", 1)[0].strip() if "->" in first else "unknown"

    def _redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.privacy.redact_logs:
            return args
        return {k: "<redacted>" for k in args}

    def _queue_approval_prompt(self, manifest, decision, *, audit_context=None, args=None):
        capability = self._decision_capability(decision)
        step = (audit_context or {}).get("step")
        reason = decision.reason
        redacted_args = self._redact_args(args or {})
        target = self._approval_target(manifest.id, args or {})
        scope = {
            "fs_scope": manifest.fs_scope,
            "needs_network": bool(manifest.needs_network),
        }

        approval_key = (self.current_job_ref or "", int(step or 0), manifest.id)
        if approval_key in self._approved_steps:
            self._approved_steps.discard(approval_key)
            return True

        if self._has_approval_grant(manifest.id, capability, scope):
            log(
                {
                    "event": "queue_grant_auto_approved",
                    "job": self.current_job_ref,
                    "step": step,
                    "skill": manifest.id,
                    "capability": capability,
                    "scope": scope,
                }
            )
            return True

        if self.auto_approve_ask and self.dev_mode and capability in _AUTO_APPROVE_ALLOWLIST:
            log(
                {
                    "event": "queue_auto_approved",
                    "job": self.current_job_ref,
                    "step": step,
                    "skill": manifest.id,
                    "reason": reason,
                    "capability": capability,
                    "target": target,
                    "scope": scope,
                }
            )
            return True

        log(
            {
                "event": "queue_approval_required",
                "job": self.current_job_ref,
                "step": step,
                "skill": manifest.id,
                "reason": reason,
                "capability": capability,
                "target": target,
                "scope": scope,
            }
        )
        return {
            "status": "pending",
            "step": step,
            "skill": manifest.id,
            "reason": reason,
            "policy_reason": reason,
            "capability": capability,
            "args": redacted_args,
            "target": target,
            "scope": scope,
        }

    def _approval_target(self, skill_id: str, args: dict[str, Any]) -> dict[str, str]:
        if skill_id == "system.open_url":
            return {"type": "url", "value": str(args.get("url", ""))}
        if skill_id == "system.open_app":
            return {"type": "app", "value": str(args.get("name", ""))}
        if skill_id in {"files.read_text", "files.write_text"}:
            return {"type": "file", "value": str(args.get("path", ""))}
        if skill_id == "sandbox.exec":
            command = args.get("command", [])
            if isinstance(command, list):
                return {"type": "command", "value": " ".join(str(c) for c in command)}
            return {"type": "command", "value": str(command)}
        return {"type": "unknown", "value": ""}

    def _grants_path(self) -> Path:
        return self.approvals / _APPROVAL_GRANTS_FILE

    def _read_grants(self) -> list[dict[str, Any]]:
        path = self._grants_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _write_grants(self, grants: list[dict[str, Any]]) -> None:
        self._grants_path().write_text(json.dumps(grants, indent=2), encoding="utf-8")

    def grant_approval_scope(self, *, skill: str, capability: str, scope: dict[str, Any]) -> None:
        self.ensure_dirs()
        grants = self._read_grants()
        normalized = {
            "skill": skill,
            "capability": capability,
            "scope": {
                "fs_scope": str(scope.get("fs_scope", "workspace_only")),
                "needs_network": bool(scope.get("needs_network", False)),
            },
            "ts": time.time(),
        }
        for item in grants:
            if (
                item.get("skill") == normalized["skill"]
                and item.get("capability") == normalized["capability"]
                and item.get("scope") == normalized["scope"]
            ):
                return
        grants.append(normalized)
        self._write_grants(grants)

    def _has_approval_grant(self, skill: str, capability: str, scope: dict[str, Any]) -> bool:
        normalized_scope = {
            "fs_scope": str(scope.get("fs_scope", "workspace_only")),
            "needs_network": bool(scope.get("needs_network", False)),
        }
        for item in self._read_grants():
            if (
                item.get("skill") == skill
                and item.get("capability") == capability
                and item.get("scope") == normalized_scope
            ):
                return True
        return False

    def ensure_dirs(self) -> None:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.done.mkdir(parents=True, exist_ok=True)
        self.failed.mkdir(parents=True, exist_ok=True)
        self.canceled.mkdir(parents=True, exist_ok=True)
        self.pending.mkdir(parents=True, exist_ok=True)
        self.approvals.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)

    def _lock_payload(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "ts": time.time(),
            "queue_root": str(self.queue_root),
        }

    def _log_lock_event(self, event: str, *, details: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "event": event,
            "ts_ms": int(time.time() * 1000),
            "pid": os.getpid(),
            "lock": str(self.lock_file.resolve()),
            "queue_root": str(self.queue_root.resolve()),
        }
        if details:
            payload["details"] = details
        log(payload)

    def _increment_health_counter(
        self,
        key: str,
        *,
        amount: int = 1,
        last_error: str | None = None,
    ) -> None:
        increment_health_counter(self.queue_root, key, amount=amount, last_error=last_error)

    def _update_daemon_health_state(self, **values: Any) -> None:
        def _apply(payload: dict[str, Any]) -> dict[str, Any]:
            payload.update(values)
            payload["updated_at_ms"] = int(time.time() * 1000)
            return payload

        update_health_snapshot(self.queue_root, _apply)

    def lock_counters_snapshot(self) -> dict[str, int]:
        payload = read_health_snapshot(self.queue_root)
        counters_raw = payload.get("counters")
        counters: dict[str, Any] = counters_raw if isinstance(counters_raw, dict) else {}
        return {str(k): int(v or 0) for k, v in counters.items()}

    def _read_lock_payload(self) -> dict[str, Any]:
        if not self.lock_file.exists():
            return {}
        try:
            return json.loads(self.lock_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _pid_is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _acquire_daemon_lock(self) -> None:
        self.ensure_dirs()
        payload = self._lock_payload()
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            existing = self._read_lock_payload()
            pid = int(existing.get("pid") or 0)
            alive = self._pid_is_alive(pid)
            self._increment_health_counter("lock_acquire_fail")
            self._update_daemon_health_state(lock_state="locked_by_other", lock_holder_pid=pid)
            record_health_error(self.queue_root, f"queue lock already held by pid={pid}")
            self._log_lock_event(
                "queue_daemon_lock_contended",
                details={"alive": alive, "existing_pid": pid},
            )
            raise QueueLockError(f"queue lock already held by pid={pid}: {self.lock_file}") from exc

        existing = self._read_lock_payload()
        existing_pid = int(existing.get("pid") or 0)
        existing_ts = float(existing.get("ts") or 0.0)
        existing_stale = (
            (time.time() - existing_ts) > self.lock_stale_after_s if existing_ts else False
        )
        existing_alive = self._pid_is_alive(existing_pid) if existing_pid else False
        if existing and (existing_stale or not existing_alive):
            self._increment_health_counter("lock_reclaimed")
            self._log_lock_event(
                "queue_daemon_lock_reclaimed",
                details={
                    "stale": existing_stale,
                    "alive": existing_alive,
                    "existing_pid": existing_pid,
                },
            )
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(payload).encode("utf-8"))
        os.fsync(fd)
        self._lock_fd = fd
        self._lock_held = True
        self._update_daemon_health_state(lock_state="active", lock_holder_pid=os.getpid())
        self._log_lock_event("queue_daemon_lock_acquired")
        self._increment_health_counter("lock_acquire_ok")
        record_health_ok(self.queue_root, "lock_acquire")

    def release_daemon_lock(self) -> None:
        if not self._lock_held:
            return
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        self._lock_held = False
        self._update_daemon_health_state(lock_state="released")
        self._increment_health_counter("lock_released")
        record_health_ok(self.queue_root, "lock_released")
        self._log_lock_event("queue_daemon_lock_released")

    def request_shutdown(self, reason: str) -> None:
        if self._shutdown_requested:
            return
        normalized = reason.upper()
        self._shutdown_requested = True
        self._shutdown_reason = normalized
        self._update_daemon_health_state(shutdown_requested=True)
        log(
            {
                "event": "queue_daemon_shutdown_requested",
                "reason": normalized,
                "job": self.current_job_ref,
                "ts_ms": int(time.time() * 1000),
            }
        )

    def _record_clean_shutdown(self, reason: str) -> None:
        snapshot = read_health_snapshot(self.queue_root)
        if snapshot.get("last_shutdown_outcome") == "failed_shutdown":
            return
        record_last_shutdown(
            self.queue_root,
            outcome="clean",
            reason=reason,
            job=self.current_job_ref,
        )

    def _record_failed_shutdown(self, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {str(exc).strip()}"
        record_last_shutdown(
            self.queue_root,
            outcome="failed_shutdown",
            reason=reason[:_SHUTDOWN_REASON_MAX_LEN],
            job=self.current_job_ref,
        )

    def try_unlock_stale(self) -> dict[str, Any]:
        if not self.lock_file.exists():
            return {"removed": False, "stale": False, "alive": False, "pid": 0, "age_s": 0}

        payload = self._read_lock_payload()
        pid = int(payload.get("pid") or 0)
        ts = float(payload.get("ts") or payload.get("timestamp") or 0.0)
        alive = self._pid_is_alive(pid)
        age_s = max(0.0, time.time() - ts) if ts else 0.0
        stale = age_s > self.lock_stale_after_s

        if stale or not alive:
            self.lock_file.unlink(missing_ok=True)
            self._increment_health_counter("unlock_ok")
            self._log_lock_event(
                "queue_daemon_unlock_ok",
                details={
                    "reason": "stale" if stale else "dead_pid",
                    "stale": stale,
                    "alive": alive,
                    "pid": pid,
                    "age_s": age_s,
                    "existing_pid": pid,
                },
            )
            return {"removed": True, "stale": stale, "alive": alive, "pid": pid, "age_s": age_s}

        self._increment_health_counter("unlock_refused")
        record_health_error(self.queue_root, f"unlock refused: lock held by live pid={pid}")
        self._log_lock_event(
            "queue_daemon_unlock_refused",
            details={
                "reason": "live_pid",
                "stale": stale,
                "alive": alive,
                "pid": pid,
                "existing_pid": pid,
            },
        )
        raise QueueLockError(
            f"Lock held by live pid={pid}. Stop daemon first, or use --force to override."
        )

    def force_unlock(self) -> bool:
        existed = self.lock_file.exists()
        self.lock_file.unlink(missing_ok=True)
        if existed:
            self._increment_health_counter("force_unlock_count")
            self._log_lock_event(
                "queue_daemon_lock_force_unlocked",
                details={"dangerous": True, "reason": "operator_force_unlock"},
            )
        return existed

    def is_paused(self) -> bool:
        return self.pause_marker.exists()

    def pause(self) -> None:
        self.ensure_dirs()
        self.pause_marker.write_text("paused\n", encoding="utf-8")
        log({"event": "queue_paused", "marker": str(self.pause_marker)})

    def resume(self) -> None:
        self.pause_marker.unlink(missing_ok=True)
        log({"event": "queue_resumed", "marker": str(self.pause_marker)})

    def _auto_relocate_legacy_jobs(self) -> int:
        moved = 0
        for job in sorted(self.queue_root.glob("*.json")):
            if job.name.startswith(".") or not self._is_primary_job_json(job):
                continue
            relocated = self._move_job(job, self.inbox)
            if relocated is None:
                continue
            moved += 1
            log(
                {
                    "event": "queue_job_autorelocate",
                    "src": str(job),
                    "dst": str(relocated),
                    "reason": "legacy_queue_root_intake",
                }
            )
        return moved

    def _auto_relocate_misplaced_pending_jobs(self) -> int:
        moved = 0
        for job in self._pending_primary_jobs():
            meta = self.pending / f"{job.stem}.pending.json"
            if meta.exists():
                continue
            relocated = self._move_job(job, self.inbox)
            if relocated is None:
                continue
            moved += 1
            log(
                {
                    "event": "queue_job_autorelocate",
                    "src": str(job),
                    "dst": str(relocated),
                    "reason": "misplaced_pending_drop",
                }
            )
        return moved

    def _job_artifacts_dir(self, job_ref: str) -> Path:
        stem = Path(job_ref).stem
        path = self.artifacts / stem
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_action_event(self, job_ref: str, event: str, **data: Any) -> None:
        path = self._job_artifacts_dir(job_ref) / "actions.jsonl"
        payload = {"event": event, "ts": time.time(), **data}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def _job_state_sidecar_path(self, job_ref: str) -> Path:
        stem = Path(job_ref).stem
        for bucket in (self.inbox, self.pending, self.done, self.failed, self.canceled):
            candidate = bucket / f"{stem}.state.json"
            if candidate.exists():
                return candidate
        job_path = Path(job_ref)
        if job_path.parent in {self.inbox, self.pending, self.done, self.failed, self.canceled}:
            return job_path.with_name(f"{stem}.state.json")
        return self.pending / f"{stem}.state.json"

    def _read_job_state(self, job_ref: str) -> dict[str, Any]:
        path = self._job_state_sidecar_path(job_ref)
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _write_job_state(self, job_ref: str, payload: dict[str, Any]) -> None:
        path = self._job_state_sidecar_path(job_ref)
        self._write_text_atomic(path, json.dumps(payload, indent=2))

    def _update_job_state(
        self,
        job_ref: str,
        *,
        lifecycle_state: str,
        payload: dict[str, Any] | None = None,
        mission: MissionTemplate | None = None,
        rr_data: dict[str, Any] | None = None,
        terminal_outcome: str | None = None,
        failure_summary: str | None = None,
        blocked_reason: str | None = None,
        approval_status: str | None = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        current = self._read_job_state(job_ref)
        started_at_ms = int(current.get("started_at_ms") or now_ms)
        raw_transitions = current.get("transitions")
        transitions: dict[str, Any] = (
            dict(raw_transitions) if isinstance(raw_transitions, dict) else {}
        )
        transitions[lifecycle_state] = now_ms

        mission_payload = (
            {
                "mission_id": mission.id,
                "title": mission.title,
                "goal": mission.goal,
            }
            if mission is not None
            else current.get("mission")
            if isinstance(current.get("mission"), dict)
            else {}
        )
        total_steps = (
            len(mission.steps) if mission is not None else int(current.get("total_steps") or 0)
        )
        rr = rr_data if isinstance(rr_data, dict) else {}
        current_step = int(rr.get("current_step_index") or current.get("current_step_index") or 0)
        last_completed = int(
            rr.get("last_completed_step") or current.get("last_completed_step") or 0
        )
        last_attempted = int(
            rr.get("last_attempted_step") or current.get("last_attempted_step") or 0
        )
        step_outcomes = (
            rr.get("step_outcomes")
            if isinstance(rr.get("step_outcomes"), list)
            else current.get("step_outcomes")
            if isinstance(current.get("step_outcomes"), list)
            else []
        )
        resolved_terminal = (
            terminal_outcome
            if terminal_outcome is not None
            else rr.get("terminal_outcome") or current.get("terminal_outcome")
        )
        if lifecycle_state == "done" and not resolved_terminal:
            resolved_terminal = "succeeded"

        snapshot = {
            "schema_version": _JOB_STATE_SCHEMA_VERSION,
            "job_id": f"{Path(job_ref).stem}.json",
            "lifecycle_state": lifecycle_state,
            "current_step_index": current_step,
            "total_steps": total_steps,
            "last_completed_step": last_completed,
            "last_attempted_step": last_attempted,
            "terminal_outcome": resolved_terminal,
            "failure_summary": failure_summary
            if failure_summary is not None
            else current.get("failure_summary"),
            "blocked_reason": blocked_reason
            if blocked_reason is not None
            else current.get("blocked_reason"),
            "approval_status": approval_status
            if approval_status is not None
            else current.get("approval_status"),
            "mission": mission_payload,
            "step_outcomes": step_outcomes,
            "started_at_ms": started_at_ms,
            "updated_at_ms": now_ms,
            "completed_at_ms": now_ms
            if lifecycle_state in {"done", "step_failed", "blocked", "canceled"}
            else None,
            "transitions": transitions,
        }
        if payload is not None:
            snapshot["payload"] = payload
        elif isinstance(current.get("payload"), dict):
            snapshot["payload"] = current["payload"]
        self._write_job_state(job_ref, snapshot)

    def _write_plan_artifact(
        self, job_ref: str, *, payload: dict[str, Any], mission: MissionTemplate
    ) -> None:
        plan = {
            "job": Path(job_ref).name,
            "payload": payload,
            "mission": {
                "id": mission.id,
                "title": mission.title,
                "goal": mission.goal,
                "notes": mission.notes,
                "steps": [{"skill_id": s.skill_id, "args": s.args} for s in mission.steps],
            },
        }
        (self._job_artifacts_dir(job_ref) / "plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )

    def _write_run_streams(self, job_ref: str, rr_data: dict[str, Any]) -> None:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        generated_files: list[str] = []
        for item in rr_data.get("results", []):
            if not isinstance(item, dict):
                continue
            output = str(item.get("output") or "")
            error = str(item.get("error") or "")
            if output:
                stdout_lines.append(output)
            if error:
                stderr_lines.append(error)
            raw_args = item.get("args")
            path_value: Any = None
            if isinstance(raw_args, dict):
                path_value = raw_args.get("path")
            if item.get("skill") == "files.write_text" and path_value:
                generated_files.append(str(path_value))
        artifact_dir = self._job_artifacts_dir(job_ref)
        (artifact_dir / "stdout.txt").write_text("\n".join(stdout_lines), encoding="utf-8")
        (artifact_dir / "stderr.txt").write_text("\n".join(stderr_lines), encoding="utf-8")
        if generated_files:
            outputs_dir = artifact_dir / "outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)
            (outputs_dir / "generated_files.json").write_text(
                json.dumps(generated_files, indent=2), encoding="utf-8"
            )

    def _move_job(self, src: Path, target_dir: Path) -> Path | None:
        target = target_dir / src.name
        if target.exists():
            ts = int(time.time() * 1000)
            target = target_dir / f"{src.stem}-{ts}{src.suffix}"
        try:
            src.replace(target)
        except FileNotFoundError:
            log(
                {
                    "event": "queue_job_already_moved",
                    "job": str(src),
                    "target_dir": str(target_dir),
                }
            )
            return None
        state_src = src.with_name(f"{src.stem}.state.json")
        state_dst = target.with_name(f"{target.stem}.state.json")
        if state_src.exists():
            with contextlib.suppress(FileNotFoundError):
                state_src.replace(state_dst)
        return target

    def _archive_sidecar(self, sidecar: Path, *, reason: str) -> None:
        if not sidecar.exists():
            return
        archive_dir = self.archive / "sidecars"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamped = archive_dir / f"{sidecar.stem}-{int(time.time() * 1000)}.json"
        moved = self._move_job(sidecar, archive_dir)
        if moved is None:
            return
        if moved.suffix != ".json":
            moved = moved.replace(stamped)
        log(
            {
                "event": "queue_sidecar_archived",
                "reason": reason,
                "from": str(sidecar),
                "to": str(moved),
            }
        )

    def _deterministic_target_path(
        self,
        target_dir: Path,
        file_name: str,
        *,
        suffix_tag: str,
    ) -> Path:
        base = Path(file_name)
        candidate = target_dir / base.name
        if not candidate.exists():
            return candidate

        index = 1
        while True:
            indexed = target_dir / f"{base.stem}-{suffix_tag}-{index}{base.suffix}"
            if not indexed.exists():
                return indexed
            index += 1

    def _safe_relative_to_queue(self, entry: Path) -> Path:
        queue_resolved = self.queue_root.resolve()
        if entry.is_symlink():
            location = entry.expanduser().absolute()
            if not location.is_relative_to(queue_resolved):
                raise ValueError(f"path escapes queue root: {entry}")
            return location.relative_to(queue_resolved)

        resolved = entry.resolve()
        if not resolved.is_relative_to(queue_resolved):
            raise ValueError(f"path escapes queue root: {entry}")
        return resolved.relative_to(queue_resolved)

    def _quarantine_startup_recovery_path(self, src: Path, recovery_root: Path) -> Path:
        relative = self._safe_relative_to_queue(src)
        destination = recovery_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(destination))
        return destination

    def _detected_inflight_pending_jobs(self) -> list[dict[str, Any]]:
        detected: list[dict[str, Any]] = []
        for job in self._pending_primary_jobs():
            detected_state_files: list[str] = []
            pending_meta = self.pending / f"{job.stem}.pending.json"
            if pending_meta.exists() and pending_meta.is_file():
                detected_state_files.append(str(pending_meta.relative_to(self.queue_root)))
            pending_state = self.pending / f"{job.stem}.state.json"
            if pending_state.exists() and pending_state.is_file():
                detected_state_files.append(str(pending_state.relative_to(self.queue_root)))

            if not detected_state_files:
                continue

            artifacts_dir = self.artifacts / job.stem
            detected_artifacts_paths: list[str] = []
            if artifacts_dir.exists() and artifacts_dir.is_dir():
                detected_artifacts_paths.append(str(artifacts_dir.relative_to(self.queue_root)))

            detected.append(
                {
                    "job": job,
                    "job_id": job.stem,
                    "original_bucket": "pending",
                    "detected_state_files": sorted(detected_state_files),
                    "detected_artifacts_paths": sorted(detected_artifacts_paths),
                }
            )
        return sorted(detected, key=lambda item: str(item["job"]))

    def _collect_orphan_approval_files(self) -> list[Path]:
        orphans: list[Path] = []
        if not self.approvals.exists():
            return orphans
        for artifact in self._iter_approval_artifacts():
            if artifact.is_dir():
                continue
            stem = artifact.stem.removesuffix(".approval")
            pending_job = self.pending / f"{stem}.json"
            if not pending_job.exists():
                orphans.append(artifact)
        return sorted(orphans)

    def _collect_orphan_state_files(self) -> list[Path]:
        search_dirs = [
            self.queue_root,
            self.inbox,
            self.pending,
            self.done,
            self.failed,
            self.canceled,
        ]
        existing_jobs = {
            p.name
            for bucket in (self.inbox, self.pending, self.done, self.failed, self.canceled)
            for p in self._primary_jobs_in_bucket(bucket)
        }
        orphans: list[Path] = []
        for directory in search_dirs:
            if not directory.exists() or not directory.is_dir():
                continue
            for state_file in sorted(directory.glob("*.state.json")):
                if not state_file.is_file() and not state_file.is_symlink():
                    continue
                job_name = f"{state_file.name.removesuffix('.state.json')}.json"
                if job_name not in existing_jobs:
                    orphans.append(state_file)
        return sorted(set(orphans), key=lambda p: str(p))

    def recover_on_startup(self, *, now_ms: int | None = None) -> dict[str, Any]:
        self.ensure_dirs()
        recovery_ts_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        recovery_root = self.queue_root / "recovery" / f"startup-{recovery_ts_ms}"

        inflight_jobs = self._detected_inflight_pending_jobs()
        failed_jobs: list[str] = []
        failed_details: list[dict[str, Any]] = []
        quarantined_paths: list[str] = []

        for item in inflight_jobs:
            job_path = item["job"]
            if not isinstance(job_path, Path) or not job_path.exists():
                continue
            target = self._deterministic_target_path(
                self.failed, job_path.name, suffix_tag="recovered"
            )
            moved = self._move_job(job_path, target.parent)
            if moved is None:
                continue
            if moved != target:
                moved = moved.replace(target)

            detail = {
                "reason": _STARTUP_RECOVERY_REASON,
                "message": _STARTUP_RECOVERY_MESSAGE,
                "original_bucket": item["original_bucket"],
                "detected_state_files": item["detected_state_files"],
                "detected_artifacts_paths": item["detected_artifacts_paths"],
            }
            self._write_failed_error_sidecar(
                moved,
                error=f"{_STARTUP_RECOVERY_REASON}: {_STARTUP_RECOVERY_MESSAGE}",
                payload=detail,
            )

            for state_file in item["detected_state_files"]:
                candidate = self.queue_root / state_file
                if candidate.exists() or candidate.is_symlink():
                    destination = self._quarantine_startup_recovery_path(candidate, recovery_root)
                    quarantined_paths.append(str(destination.relative_to(self.queue_root)))

            approval = self.approvals / f"{job_path.stem}.approval.json"
            if approval.exists() or approval.is_symlink():
                destination = self._quarantine_startup_recovery_path(approval, recovery_root)
                quarantined_paths.append(str(destination.relative_to(self.queue_root)))

            failed_jobs.append(moved.stem)
            failed_details.append(
                {
                    "job_id": moved.stem,
                    "failed_path": str(moved.relative_to(self.queue_root)),
                    "reason": _STARTUP_RECOVERY_REASON,
                    "message": _STARTUP_RECOVERY_MESSAGE,
                    "original_bucket": item["original_bucket"],
                    "detected_state_files": item["detected_state_files"],
                    "detected_artifacts_paths": item["detected_artifacts_paths"],
                }
            )

        orphan_approvals = self._collect_orphan_approval_files()
        orphan_states = self._collect_orphan_state_files()
        for src in [*orphan_approvals, *orphan_states]:
            if not (src.exists() or src.is_symlink()):
                continue
            destination = self._quarantine_startup_recovery_path(src, recovery_root)
            quarantined_paths.append(str(destination.relative_to(self.queue_root)))

        counts = {
            "jobs_failed": len(failed_jobs),
            "orphan_approvals_quarantined": len(orphan_approvals),
            "orphan_state_files_quarantined": len(orphan_states),
            "total_quarantined": len(quarantined_paths),
        }
        summary = (
            "startup recovery complete: "
            f"jobs_failed={counts['jobs_failed']}, "
            f"orphan_approvals_quarantined={counts['orphan_approvals_quarantined']}, "
            f"orphan_state_files_quarantined={counts['orphan_state_files_quarantined']}"
        )
        report = {
            "ts_ms": recovery_ts_ms,
            "policy": "fail_fast",
            "reason": _STARTUP_RECOVERY_REASON,
            "message": _STARTUP_RECOVERY_MESSAGE,
            "counts": counts,
            "jobs_failed": sorted(failed_jobs),
            "failed_details": sorted(failed_details, key=lambda item: str(item["job_id"])),
            "quarantined_paths": sorted(quarantined_paths),
            "recovery_dir": (
                str(recovery_root.relative_to(self.queue_root)) if quarantined_paths else None
            ),
        }

        self._increment_health_counter("startup_recovery_runs")
        if counts["jobs_failed"]:
            self._increment_health_counter(
                "startup_recovery_jobs_failed", amount=counts["jobs_failed"]
            )
        if counts["total_quarantined"]:
            self._increment_health_counter(
                "startup_recovery_orphans_quarantined", amount=counts["total_quarantined"]
            )
        self._update_daemon_health_state(
            last_startup_recovery_ts=recovery_ts_ms,
            last_startup_recovery_counts=counts,
            last_startup_recovery_summary=summary,
        )
        log(
            {
                "event": "daemon_startup_recovery",
                "ts_ms": recovery_ts_ms,
                "policy": "fail_fast",
                "reason": _STARTUP_RECOVERY_REASON,
                "counts": counts,
                "affected_job_ids": sorted(failed_jobs),
                "action": "failed_or_quarantined",
                "recovery_dir": report["recovery_dir"],
            }
        )
        return report

    def _failed_error_sidecar(self, failed_job: Path) -> Path:
        return failed_job.with_name(f"{failed_job.stem}.error.json")

    def _validate_failed_error_sidecar(
        self, payload: Any, *, expected_job: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("failed sidecar payload must be a JSON object")
        required = ("schema_version", "job", "error", "timestamp_ms")
        missing = [field for field in required if field not in payload]
        if missing:
            raise ValueError(f"failed sidecar missing required fields: {', '.join(missing)}")
        self._validate_failed_sidecar_schema_version(payload.get("schema_version"), mode="read")
        job = payload.get("job")
        if not isinstance(job, str) or not job:
            raise ValueError("failed sidecar field 'job' must be a non-empty string")
        if expected_job is not None and job != expected_job:
            raise ValueError(f"failed sidecar job mismatch: expected {expected_job}, got {job}")
        err = payload.get("error")
        if not isinstance(err, str) or not err:
            raise ValueError("failed sidecar field 'error' must be a non-empty string")
        timestamp_ms = payload.get("timestamp_ms")
        if not isinstance(timestamp_ms, int) or timestamp_ms < _FAILED_TIMESTAMP_MS_MIN:
            raise ValueError(
                "failed sidecar field 'timestamp_ms' must be an epoch timestamp in milliseconds"
            )
        if (
            "payload" in payload
            and payload["payload"] is not None
            and not isinstance(payload["payload"], dict)
        ):
            raise ValueError("failed sidecar field 'payload' must be an object when present")
        return payload

    def _read_failed_error_sidecar(self, failed_job: Path) -> dict[str, Any] | None:
        sidecar_path = self._failed_error_sidecar(failed_job)
        if not sidecar_path.exists():
            return None
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
            return self._validate_failed_error_sidecar(data, expected_job=failed_job.name)
        except Exception as exc:
            log(
                {
                    "event": "queue_failed_sidecar_invalid",
                    "job": failed_job.name,
                    "path": str(sidecar_path),
                    "error": repr(exc),
                }
            )
            return None

    def _validate_failed_sidecar_schema_version(self, version: Any, *, mode: str) -> int:
        if not isinstance(version, int):
            raise ValueError("failed sidecar field 'schema_version' must be an integer")
        if mode == "read":
            if version in _FAILED_SIDECAR_SCHEMA_READ_VERSIONS:
                return version
            raise ValueError(
                "unsupported failed sidecar schema version for read: "
                f"{version} (supported: {sorted(_FAILED_SIDECAR_SCHEMA_READ_VERSIONS)})"
            )
        if mode == "write":
            if version == _FAILED_SIDECAR_SCHEMA_WRITE_VERSION:
                return version
            raise ValueError(
                "unsupported failed sidecar schema version for write: "
                f"{version} (writer pinned to {_FAILED_SIDECAR_SCHEMA_WRITE_VERSION})"
            )
        raise ValueError(f"invalid schema-version validation mode: {mode}")

    def _write_failed_error_sidecar(
        self, failed_job: Path, *, error: str, payload: dict[str, Any] | None = None
    ) -> None:
        details: dict[str, Any] = {
            "schema_version": _FAILED_SIDECAR_SCHEMA_WRITE_VERSION,
            "job": failed_job.name,
            "error": error,
            "timestamp_ms": int(time.time() * 1000),
        }
        self._validate_failed_sidecar_schema_version(details["schema_version"], mode="write")
        if payload is not None:
            details["payload"] = payload
        self._validate_failed_error_sidecar(details, expected_job=failed_job.name)
        self._write_text_atomic(
            self._failed_error_sidecar(failed_job), json.dumps(details, indent=2)
        )

    def prune_failed_artifacts(
        self, *, max_age_s: float | None = None, max_count: int | None = None
    ) -> dict[str, int]:
        self.ensure_dirs()
        max_age_s = self.failed_retention_max_age_s if max_age_s is None else max_age_s
        max_count = self.failed_retention_max_count if max_count is None else max_count

        primary_jobs = [p for p in self.failed.glob("*.json") if self._is_primary_job_json(p)]
        sidecars = list(self.failed.glob("*.error.json"))
        units: dict[str, dict[str, Any]] = {}

        for job in primary_jobs:
            key = job.stem
            unit = units.setdefault(key, {"key": key, "job": None, "sidecar": None})
            unit["job"] = job
        for sidecar in sidecars:
            key = sidecar.stem.removesuffix(".error")
            unit = units.setdefault(key, {"key": key, "job": None, "sidecar": None})
            unit["sidecar"] = sidecar

        def _unit_newest_mtime(unit: dict[str, Any]) -> float:
            mtimes = [
                p.stat().st_mtime
                for p in (unit.get("job"), unit.get("sidecar"))
                if p is not None and p.exists()
            ]
            return max(mtimes) if mtimes else 0.0

        ordered = sorted(
            units.values(), key=lambda unit: (_unit_newest_mtime(unit), unit["key"]), reverse=True
        )
        keep_keys: set[str] = {unit["key"] for unit in ordered}

        if max_age_s is not None and max_age_s > 0:
            cutoff = time.time() - max_age_s
            keep_keys = {unit["key"] for unit in ordered if _unit_newest_mtime(unit) >= cutoff}

        if max_count is not None and max_count >= 0:
            age_filtered = [unit for unit in ordered if unit["key"] in keep_keys]
            keep_keys = {unit["key"] for unit in age_filtered[:max_count]}

        def _coerce_path(unit: dict[str, Any], key: str) -> Path | None:
            value = unit.get(key)
            if value is None:
                return None
            if isinstance(value, Path):
                return value
            if isinstance(value, str):
                return Path(value)
            raise ValueError(f"failed artifact unit field {key!r} must be a path")

        removed_jobs = 0
        removed_sidecars = 0
        for unit in ordered:
            if unit["key"] in keep_keys:
                continue
            job_path = _coerce_path(unit, "job")
            sidecar_path = _coerce_path(unit, "sidecar")
            if job_path is not None and job_path.exists():
                job_path.unlink()
                removed_jobs += 1
            if sidecar_path is not None and sidecar_path.exists():
                sidecar_path.unlink()
                removed_sidecars += 1

        if removed_jobs or removed_sidecars:
            log(
                {
                    "event": "queue_failed_artifacts_pruned",
                    "removed_jobs": removed_jobs,
                    "removed_sidecars": removed_sidecars,
                    "max_age_s": max_age_s,
                    "max_count": max_count,
                }
            )
        return {"removed_jobs": removed_jobs, "removed_sidecars": removed_sidecars}

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        mission_id = payload.get("mission_id", payload.get("mission"))
        goal = payload.get("goal") if "goal" in payload else payload.get("plan_goal")
        normalized: dict[str, Any] = {}
        if mission_id is not None:
            normalized["mission_id"] = str(mission_id)
        if goal is not None:
            normalized["goal"] = str(goal)

        title = payload.get("title")
        if title is not None:
            normalized["title"] = str(title)

        steps = payload.get("steps")
        if steps is not None:
            normalized["steps"] = steps

        if "approval_required" in payload:
            normalized["approval_required"] = payload.get("approval_required") is True

        return normalized

    def _build_inline_mission(self, payload: dict[str, Any], *, job_ref: str) -> MissionTemplate:
        steps_raw = payload.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError("job steps must be a non-empty list")

        mission_steps: list[MissionStep] = []
        for idx, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"job step {idx} must be an object")

            skill_id_raw = item.get("skill_id", item.get("skill"))
            skill_id = str(skill_id_raw or "").strip()
            if not skill_id:
                raise ValueError(
                    f"job step {idx} missing skill_id (or legacy skill) for {Path(job_ref).name}"
                )

            args_raw = item.get("args", {})
            if args_raw is None:
                args_raw = {}
            if not isinstance(args_raw, dict):
                raise ValueError(f"job step {idx} args must be an object")

            mission_steps.append(MissionStep(skill_id=skill_id, args=dict(args_raw)))

        mission_id = Path(job_ref).stem
        title = str(payload.get("title") or f"Queued Mission {mission_id}")
        goal = str(payload.get("goal") or "User-defined queued mission")
        return MissionTemplate(
            id=mission_id,
            title=title,
            goal=goal,
            notes="inline_queue_job",
            steps=mission_steps,
        )

    def _apply_brain_backoff_before_plan_attempt(self) -> None:
        """Sleep once before a planning attempt when failure backoff is active."""
        snapshot = read_health_snapshot(self.queue_root)
        wait_s = compute_brain_backoff_s(snapshot.get("consecutive_brain_failures", 0))
        if wait_s <= 0:
            return
        time.sleep(wait_s)
        record_brain_backoff_applied(
            self.queue_root,
            wait_s=wait_s,
            now_ts=time.time(),
        )

    def _build_mission_for_payload(
        self, payload: dict[str, Any], *, job_ref: str
    ) -> MissionTemplate:
        normalized = self._normalize_payload(payload)
        snapshot = generate_capabilities_snapshot(self.mission_runner.skill_runner.registry)
        if "mission_id" in normalized:
            validate_mission_id_against_snapshot(normalized["mission_id"], snapshot)
            mission = get_mission(normalized["mission_id"])
            validate_mission_steps_against_snapshot(mission, snapshot)
            return mission
        if "steps" in normalized:
            mission = self._build_inline_mission(normalized, job_ref=job_ref)
            validate_mission_steps_against_snapshot(mission, snapshot)
            return mission
        if "goal" in normalized:
            try:
                self._apply_brain_backoff_before_plan_attempt()
                mission = asyncio.run(
                    plan_mission(
                        goal=normalized["goal"],
                        cfg=self.cfg,
                        registry=self.mission_runner.skill_runner.registry,
                        source="queue",
                        job_ref=job_ref,
                        queue_root=self.queue_root,
                    )
                )
                validate_mission_steps_against_snapshot(mission, snapshot)
                return mission
            except MissionPlannerError as exc:
                raise RuntimeError(str(exc)) from exc
        raise ValueError(
            "job must contain mission_id (or mission), goal (or plan_goal), or inline steps"
        )

    def _write_pending_artifacts(
        self,
        job_in_pending: Path,
        *,
        payload: dict[str, Any],
        mission: MissionTemplate,
        run_data: dict[str, Any],
    ) -> None:
        step = int(run_data.get("step", 0) or 0)
        approval = {
            "job": job_in_pending.name,
            "job_path": str(job_in_pending),
            "job_id": job_in_pending.stem,
            "mission_id": payload.get("mission_id"),
            "goal": payload.get("goal"),
            "step": step,
            "skill": run_data.get("skill"),
            "args": run_data.get("args", {}),
            "reason": run_data.get("reason"),
            "policy_reason": run_data.get("policy_reason", run_data.get("reason")),
            "capability": run_data.get("capability"),
            "target": run_data.get("target", {"type": "unknown", "value": ""}),
            "fs_scope": (run_data.get("scope") or {}).get("fs_scope", "workspace_only"),
            "needs_network": bool((run_data.get("scope") or {}).get("needs_network", False)),
            "scope": {
                "fs_scope": (run_data.get("scope") or {}).get("fs_scope", "workspace_only"),
                "needs_network": bool((run_data.get("scope") or {}).get("needs_network", False)),
            },
            "status": "pending_approval",
            "ts": time.time(),
        }
        artifact_path = self.approvals / f"{job_in_pending.stem}.approval.json"
        artifact_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
        self._notify_pending_approval(approval)

        meta = {
            "status": "pending_approval",
            "lifecycle_state": "awaiting_approval",
            "job": job_in_pending.name,
            "payload": payload,
            "resume_step": step,
            "current_step_index": step,
            "last_completed_step": max(step - 1, 0),
            "last_attempted_step": step,
            "total_steps": len(mission.steps),
            "approval_status": "pending",
            "mission": {
                "id": mission.id,
                "title": mission.title,
                "goal": mission.goal,
                "notes": mission.notes,
                "steps": [{"skill_id": s.skill_id, "args": s.args} for s in mission.steps],
            },
        }
        (self.pending / f"{job_in_pending.stem}.pending.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    def _is_hard_approval_required(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("approval_required") is True)

    def _ensure_hard_approval_gate(self, job_path: Path, *, payload: dict[str, Any]) -> bool:
        if not self._is_hard_approval_required(payload):
            self._increment_health_counter("approval_gate_skipped_no_flag")
            log({"event": "queue_approval_gate_skipped_no_flag", "job": str(job_path)})
            return False

        if job_path.parent == self.inbox:
            moved = self._move_job(job_path, self.pending)
            if moved is None:
                return True
            job_in_pending = moved
        else:
            job_in_pending = job_path

        artifact_path = self.approvals / f"{job_in_pending.stem}.approval.json"
        meta_path = self.pending / f"{job_in_pending.stem}.pending.json"

        if artifact_path.exists():
            self._increment_health_counter("approval_gate_already_present")
            meta_path.unlink(missing_ok=True)
            log(
                {
                    "event": "queue_approval_gate_already_present",
                    "job": str(job_in_pending),
                    "artifact": str(artifact_path),
                }
            )
            return True

        approval = {
            "job": job_in_pending.name,
            "job_path": str(job_in_pending),
            "job_id": job_in_pending.stem,
            "mission_id": payload.get("mission_id"),
            "goal": payload.get("goal"),
            "step": 0,
            "skill": "approval_required",
            "args": {},
            "reason": "approval_required=true hard gate",
            "policy_reason": "approval_required=true hard gate",
            "capability": "approval_required",
            "target": {"type": "unknown", "value": ""},
            "fs_scope": "workspace_only",
            "needs_network": False,
            "scope": {"fs_scope": "workspace_only", "needs_network": False},
            "status": "pending_approval",
            "ts": time.time(),
        }
        self._write_text_atomic(artifact_path, json.dumps(approval, indent=2))
        meta_path.unlink(missing_ok=True)
        self._update_job_state(
            str(job_in_pending),
            lifecycle_state="awaiting_approval",
            payload=payload,
            rr_data={"current_step_index": 0, "last_completed_step": 0, "last_attempted_step": 0},
            approval_status="pending",
        )
        self._notify_pending_approval(approval)
        self._increment_health_counter("approval_gate_created")
        self._write_action_event(str(job_in_pending), "queue_job_pending_approval", step=0)
        log(
            {
                "event": "queue_approval_gate_created",
                "job": str(job_in_pending),
                "reason": "approval_required=true hard gate",
            }
        )
        log(
            {
                "event": "queue_job_pending_approval",
                "job": str(job_in_pending),
                "step": 0,
                "reason": "approval_required=true hard gate",
            }
        )
        return True

    def _notify_pending_approval(self, approval: dict[str, Any]) -> None:
        notify_override = os.getenv("VOXERA_NOTIFY")
        notify_enabled = (
            self.settings.notify_enabled if notify_override is None else notify_override == "1"
        )
        if not notify_enabled:
            return

        job = str(approval.get("job") or approval.get("job_id") or "unknown-job")
        skill = str(approval.get("skill") or "unknown-skill")
        reason = str(approval.get("reason") or "approval required")
        try:
            result = subprocess.run(
                [
                    "notify-send",
                    "Voxera approval pending",
                    f"{job} · {skill}\n{reason}",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                log({"event": "queue_notify_sent", "job": job, "skill": skill, "reason": reason})
                return
            stderr = (
                result.stderr or ""
            ).strip() or f"notify-send exited with code {result.returncode}"
            log(
                {
                    "event": "queue_notify_failed",
                    "job": job,
                    "skill": skill,
                    "reason": reason,
                    "error": stderr,
                }
            )
        except Exception as exc:
            log(
                {
                    "event": "queue_notify_failed",
                    "job": job,
                    "skill": skill,
                    "reason": reason,
                    "error": repr(exc),
                }
            )

    def _is_snapshot_artifact(self, path: Path) -> bool:
        name = path.name
        if not name.endswith(".json"):
            return False
        if name == "config_snapshot.json":
            return True
        if name.startswith("config_snapshot"):
            return True
        return "_ops" in path.parts

    def _is_ready_job_file(self, path: Path) -> bool:
        if path.parent != self.inbox or not path.is_file():
            return False
        if self._is_snapshot_artifact(path):
            return False
        name = path.name
        if not name.endswith(".json"):
            return False
        if name.startswith("."):
            return False
        blocked_suffixes = (
            ".pending.json",
            ".approval.json",
            ".state.json",
            ".tmp.json",
            ".partial.json",
        )
        return not name.endswith(blocked_suffixes)

    def _load_job_payload_with_retry(self, job_path: Path) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, _PARSE_RETRY_ATTEMPTS + 1):
            try:
                payload = json.loads(job_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("job payload must be a JSON object")
                if attempt > 1:
                    log(
                        {
                            "event": "queue_job_parse_stabilized",
                            "attempt": attempt,
                            "path": str(job_path),
                        }
                    )
                return payload
            except json.JSONDecodeError as exc:
                last_error = exc
                if attempt >= _PARSE_RETRY_ATTEMPTS:
                    break
                log({"event": "queue_job_retry_parse", "attempt": attempt, "path": str(job_path)})
                time.sleep(_PARSE_RETRY_BACKOFF_S)

        if last_error is not None:
            raise last_error
        raise ValueError("job payload must be a JSON object")

    def _is_job_state_sidecar(self, path: Path) -> bool:
        return path.name.endswith(".state.json")

    def _is_metadata_sidecar(self, path: Path) -> bool:
        return path.name.endswith(
            (
                ".pending.json",
                ".approval.json",
                ".error.json",
                ".state.json",
                ".tmp.json",
                ".partial.json",
            )
        )

    def _count_files(self, directory: Path, pattern: str) -> int:
        if not directory.exists():
            return 0
        return sum(1 for _ in directory.glob(pattern))

    def _is_primary_job_json(self, path: Path) -> bool:
        return (
            path.name.endswith(".json")
            and path.name != "health.json"
            and not self._is_snapshot_artifact(path)
            and not self._is_metadata_sidecar(path)
        )

    def _primary_jobs_in_bucket(self, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return sorted(
            p for p in directory.glob("*.json") if p.is_file() and self._is_primary_job_json(p)
        )

    def _pending_primary_jobs(self) -> list[Path]:
        return self._primary_jobs_in_bucket(self.pending)

    def _approval_ref_variants(self, path: Path) -> set[str]:
        stem = path.stem
        if stem.endswith(".approval"):
            stem = stem[: -len(".approval")]
        base = stem.removeprefix("job-")
        return {
            stem,
            base,
            f"{stem}.json",
            f"{base}.json",
            f"{stem}.approval",
            f"{stem}.approval.json",
            path.name,
        }

    def _canonical_job_name(self, artifact: Path, data: dict[str, Any]) -> str:
        job = Path(str(data.get("job") or "")).name
        if job and not job.endswith(".approval.json"):
            return job
        stem = artifact.stem.removesuffix(".approval")
        return f"{stem}.json"

    def _iter_approval_artifacts(self) -> list[Path]:
        if not self.approvals.exists():
            return []
        return sorted(
            self.approvals.glob("*.approval.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )

    def _approval_scope_from_artifact(self, data: dict[str, Any]) -> dict[str, Any]:
        nested_raw = data.get("scope")
        nested: dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
        fs_scope = data.get("fs_scope", nested.get("fs_scope", "workspace_only"))
        needs_network = data.get("needs_network", nested.get("needs_network", False))
        return {"fs_scope": str(fs_scope), "needs_network": bool(needs_network)}

    def _read_approval_artifact(self, artifact: Path) -> dict[str, Any]:
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("approval artifact must be a JSON object")
            scope = self._approval_scope_from_artifact(data)
            data["_artifact"] = artifact.name
            data["job"] = self._canonical_job_name(artifact, data)
            data["approve_refs"] = [
                data["job"],
                Path(data["job"]).stem.removeprefix("job-"),
                str((self.pending / data["job"]).resolve()),
            ]
            data.setdefault("target", {"type": "unknown", "value": ""})
            data["scope"] = scope
            data["fs_scope"] = scope["fs_scope"]
            data["needs_network"] = scope["needs_network"]
            data.setdefault("policy_reason", data.get("reason", ""))
            return data
        except Exception as exc:
            log(
                {
                    "event": "queue_status_parse_failed",
                    "filename": artifact.name,
                    "error": repr(exc),
                }
            )
            return {
                "job": artifact.name,
                "step": "-",
                "skill": "(unparseable approval artifact)",
                "reason": repr(exc),
                "capability": "-",
                "_artifact": artifact.name,
            }

    def pending_approvals_snapshot(self, *, limit: int = 10) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        artifacts = self._iter_approval_artifacts()
        for artifact in artifacts[:limit]:
            data = self._read_approval_artifact(artifact)
            out.append(
                {
                    "job": data.get("job", ""),
                    "step": data.get("step", ""),
                    "skill": data.get("skill", ""),
                    "reason": data.get("reason", ""),
                    "policy_reason": data.get("policy_reason", data.get("reason", "")),
                    "capability": data.get("capability", ""),
                    "target": data.get("target", {"type": "unknown", "value": ""}),
                    "scope": data.get("scope", {}),
                    "fs_scope": data.get("fs_scope", "workspace_only"),
                    "needs_network": bool(data.get("needs_network", False)),
                }
            )
        return out

    def _failed_job_files_snapshot(self) -> list[Path]:
        if not self.failed.exists():
            return []
        return sorted(
            (p for p in self.failed.glob("*.json") if self._is_primary_job_json(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def _failed_sidecar_snapshot(
        self, failed_files: list[Path]
    ) -> tuple[dict[str, dict[str, Any] | None], dict[str, int]]:
        sidecars_by_job: dict[str, dict[str, Any] | None] = {}
        valid = 0
        invalid = 0
        missing = 0

        for failed_job in failed_files:
            sidecar = self._failed_error_sidecar(failed_job)
            if not sidecar.exists():
                sidecars_by_job[failed_job.name] = None
                missing += 1
                continue

            payload = self._read_failed_error_sidecar(failed_job)
            sidecars_by_job[failed_job.name] = payload
            if payload is None:
                invalid += 1
            else:
                valid += 1

        return sidecars_by_job, {
            "failed_sidecars_valid": valid,
            "failed_sidecars_invalid": invalid,
            "failed_sidecars_missing": missing,
        }

    def recent_failed_jobs_snapshot(
        self,
        *,
        limit: int = 10,
        audit_tail: int = 200,
        failed_files: list[Path] | None = None,
        sidecars_by_job: dict[str, dict[str, Any] | None] | None = None,
    ) -> list[dict[str, Any]]:
        files = failed_files if failed_files is not None else self._failed_job_files_snapshot()

        error_by_job: dict[str, str] = {}
        for event in reversed(tail(audit_tail)):
            if event.get("event") != "queue_job_failed":
                continue
            job = Path(str(event.get("job", ""))).name
            if not job or job in error_by_job:
                continue
            error_by_job[job] = str(event.get("error") or "")

        resolved_sidecars = sidecars_by_job or {}
        rows: list[dict[str, Any]] = []
        for item in files[:limit]:
            sidecar_payload = resolved_sidecars.get(item.name)
            if sidecar_payload is None and item.name not in resolved_sidecars:
                sidecar_payload = self._read_failed_error_sidecar(item)
            sidecar_error = str(sidecar_payload.get("error") or "") if sidecar_payload else ""
            rows.append(
                {"job": item.name, "error": sidecar_error or error_by_job.get(item.name, "")}
            )
        return rows

    def _latest_failed_prune_snapshot(self, *, audit_tail: int = 200) -> dict[str, Any]:
        for event in reversed(tail(audit_tail)):
            if event.get("event") != "queue_failed_artifacts_pruned":
                continue
            return {
                "removed_jobs": int(event.get("removed_jobs", 0) or 0),
                "removed_sidecars": int(event.get("removed_sidecars", 0) or 0),
                "max_age_s": event.get("max_age_s"),
                "max_count": event.get("max_count"),
            }
        return {
            "removed_jobs": 0,
            "removed_sidecars": 0,
            "max_age_s": None,
            "max_count": None,
        }

    def status_snapshot(
        self, *, approvals_limit: int = 10, failed_limit: int = 10
    ) -> dict[str, Any]:
        failed_files = self._failed_job_files_snapshot()
        sidecars_by_job, sidecar_health = self._failed_sidecar_snapshot(failed_files)
        retention = {
            "max_age_s": self.failed_retention_max_age_s,
            "max_count": self.failed_retention_max_count,
        }
        lock_payload = self._read_lock_payload()
        lock_pid = int(lock_payload.get("pid") or 0)
        lock_alive = self._pid_is_alive(lock_pid)
        health = read_health_snapshot(self.queue_root)
        return {
            "queue_root": str(self.queue_root),
            "exists": self.queue_root.exists(),
            "counts": {
                "inbox": len(self._primary_jobs_in_bucket(self.inbox)),
                "pending": len(self._pending_primary_jobs()),
                "pending_approvals": self._count_files(self.approvals, "*.approval.json"),
                "done": len(self._primary_jobs_in_bucket(self.done)),
                "failed": len(failed_files),
                "canceled": len(self._primary_jobs_in_bucket(self.canceled)),
            },
            **sidecar_health,
            "failed_retention": retention,
            "failed_prune_last": self._latest_failed_prune_snapshot(),
            "pending_approvals": self.pending_approvals_snapshot(limit=approvals_limit),
            "recent_failed": self.recent_failed_jobs_snapshot(
                limit=failed_limit, failed_files=failed_files, sidecars_by_job=sidecars_by_job
            ),
            "artifacts_root": str(self.artifacts),
            "intake_glob": str(self.inbox / "*.json"),
            "paused": self.is_paused(),
            "daemon_lock_counters": self.lock_counters_snapshot(),
            "health_path": str((self.queue_root / "health.json").resolve()),
            "lock_status": {
                "exists": self.lock_file.exists(),
                "lock_path": str(self.lock_file.resolve()),
                "pid": lock_pid,
                "alive": lock_alive,
            },
            "daemon_state": health.get("daemon_state", "healthy"),
            "daemon_started_at_ms": health.get("daemon_started_at_ms"),
            "daemon_pid": health.get("daemon_pid"),
            "updated_at_ms": health.get("updated_at_ms"),
            "lock_state": health.get("lock_state"),
            "consecutive_brain_failures": health.get("consecutive_brain_failures", 0),
            "brain_backoff_wait_s": health.get("brain_backoff_wait_s", 0),
            "brain_backoff_active": bool(health.get("brain_backoff_active", False)),
            "brain_backoff_last_applied_s": health.get("brain_backoff_last_applied_s", 0),
            "brain_backoff_last_applied_ts": health.get("brain_backoff_last_applied_ts"),
            "degraded_since_ts": health.get("degraded_since_ts"),
            "degraded_reason": health.get("degraded_reason"),
            "last_shutdown_ts": health.get("last_shutdown_ts"),
            "last_shutdown_reason": health.get("last_shutdown_reason"),
            "last_shutdown_job": health.get("last_shutdown_job"),
            "last_shutdown_outcome": health.get("last_shutdown_outcome"),
            "last_error": health.get("last_error", ""),
            "last_error_ts_ms": health.get("last_error_ts_ms"),
            "last_ok_event": health.get("last_ok_event", ""),
            "last_ok_ts_ms": health.get("last_ok_ts_ms"),
            "last_fallback_reason": health.get("last_fallback_reason"),
            "last_fallback_from": health.get("last_fallback_from"),
            "last_fallback_to": health.get("last_fallback_to"),
            "last_fallback_ts_ms": health.get("last_fallback_ts_ms"),
            "health_counters": health.get("counters")
            if isinstance(health.get("counters"), dict)
            else {},
            "panel_auth": health.get("panel_auth")
            if isinstance(health.get("panel_auth"), dict)
            else {},
        }

    def _resolve_job_ref_in_dirs(self, ref: str, directories: list[Path]) -> Path | None:
        raw = ref.strip()
        if not raw:
            return None
        direct = Path(raw).expanduser()
        if direct.exists() and direct.is_file() and self._is_primary_job_json(direct):
            return direct
        base = Path(raw).name
        stem = Path(base).stem
        candidates = {base, f"{stem}.json", f"job-{stem}.json"}
        for directory in directories:
            for cand in candidates:
                path = directory / cand
                if path.exists() and path.is_file() and self._is_primary_job_json(path):
                    return path
        return None

    def cancel_job(self, ref: str) -> Path:
        self.ensure_dirs()
        job = self._resolve_job_ref_in_dirs(ref, [self.inbox, self.pending])
        if job is None:
            raise FileNotFoundError(f"job not found: {ref}")

        moved = self._move_job(job, self.canceled)
        if moved is None:
            raise FileNotFoundError(f"job not found: {ref}")
        (self.pending / f"{moved.stem}.pending.json").unlink(missing_ok=True)
        (self.approvals / f"{moved.stem}.approval.json").unlink(missing_ok=True)
        self._archive_sidecar(self._failed_error_sidecar(self.failed / moved.name), reason="cancel")
        self._update_job_state(
            str(moved),
            lifecycle_state="canceled",
            terminal_outcome="canceled",
        )
        log({"event": "queue_job_cancel", "ref": ref, "job": str(moved)})
        return moved

    def retry_job(self, ref: str) -> Path:
        self.ensure_dirs()
        source_job = self._resolve_job_ref_in_dirs(ref, [self.failed, self.canceled])
        if source_job is None:
            raise FileNotFoundError(f"retry job not found: {ref}")
        self._archive_sidecar(self._failed_error_sidecar(source_job), reason="retry")
        moved = self._move_job(source_job, self.inbox)
        if moved is None:
            raise FileNotFoundError(f"retry job not found: {ref}")
        log(
            {
                "event": "queue_job_retry",
                "source_job": str(source_job),
                "new_attempt": str(moved),
            }
        )
        return moved

    def delete_terminal_job(self, ref: str, *, confirm: str) -> str:
        self.ensure_dirs()
        job = self._resolve_job_ref_in_dirs(ref, [self.done, self.failed, self.canceled])
        if job is None:
            raise FileNotFoundError(f"terminal job not found: {ref}")
        if confirm != job.name:
            raise ValueError("delete confirmation mismatch")

        job.unlink(missing_ok=True)
        if job.parent == self.failed:
            self._failed_error_sidecar(job).unlink(missing_ok=True)
        artifacts_dir = self.artifacts / job.stem
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir)
        (self.pending / f"{job.stem}.pending.json").unlink(missing_ok=True)
        (self.approvals / f"{job.stem}.approval.json").unlink(missing_ok=True)
        log({"event": "queue_job_deleted", "job": str(job), "artifacts_dir": str(artifacts_dir)})
        return job.name

    def _shutdown_failure_payload(self) -> dict[str, Any]:
        return {
            "reason": "shutdown",
            "message": "daemon shutdown requested",
            "signal": self._shutdown_reason or "UNKNOWN",
        }

    def _finalize_job_shutdown_failure(
        self,
        job_path: Path,
        *,
        signal_reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Path | None:
        moved = self._move_job(job_path, self.failed)
        if moved is None:
            return None
        shutdown_payload = self._shutdown_failure_payload()
        reason = shutdown_payload["reason"]
        message = shutdown_payload["message"]
        self._write_failed_error_sidecar(
            moved,
            error=f"{reason}: {message}",
            payload={**(payload or {}), "shutdown": shutdown_payload},
        )
        self._update_job_state(
            str(moved),
            lifecycle_state="step_failed",
            payload=payload if isinstance(payload, dict) else None,
            terminal_outcome="failed",
            failure_summary=f"{reason}: {message}",
        )
        self.stats.failed += 1
        self._write_action_event(str(moved), "queue_job_failed", error=f"{reason}: {message}")
        self._update_daemon_health_state(shutdown_requested=True)
        record_last_shutdown(
            self.queue_root,
            outcome="failed_shutdown",
            reason=signal_reason or self._shutdown_reason or "UNKNOWN",
            job=moved.name,
        )
        log(
            {
                "event": "queue_job_failed_shutdown",
                "job": str(moved),
                "error": reason,
                "message": message,
                "signal": signal_reason or self._shutdown_reason,
            }
        )
        self.prune_failed_artifacts()
        return moved

    def _assistant_response_artifact_path(self, job_ref: str) -> Path:
        return self._job_artifacts_dir(job_ref) / "assistant_response.json"

    def _create_assistant_brain(self, provider) -> OpenAICompatBrain | GeminiBrain:
        if provider.type == "openai_compat":
            return OpenAICompatBrain(
                model=provider.model,
                base_url=provider.base_url or "https://openrouter.ai/api/v1",
                api_key_ref=provider.api_key_ref,
                extra_headers=provider.extra_headers,
            )
        if provider.type == "gemini":
            return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
        raise ValueError(f"unsupported assistant provider type: {provider.type}")

    def _assistant_error_class(self, exc: Exception) -> str:
        return type(exc).__name__

    def _assistant_fallback_reason(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:
                return "rate_limited"
            if status in {401, 403}:
                return "provider_auth"
            if status >= 500:
                return "provider_transient"
            return "provider_http_error"
        if isinstance(exc, httpx.HTTPError):
            return "provider_network"
        if "rate limit" in msg or "429" in msg:
            return "rate_limited"
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if any(tok in msg for tok in {"api key", "unauthorized", "forbidden", "auth"}):
            return "provider_auth"
        if any(
            tok in msg for tok in {"malformed", "non-json", "empty content", "missing candidates"}
        ):
            return "malformed_response"
        if any(
            tok in msg for tok in {"connection", "network", "temporar", "unavailable", "503", "502"}
        ):
            return "provider_transient"
        return "non_fallback_error"

    def _assistant_should_fallback(self, exc: Exception) -> bool:
        return self._assistant_fallback_reason(exc) != "non_fallback_error"

    def _assistant_answer_via_brain(
        self, question: str, context: dict[str, Any], *, thread_turns: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not self.cfg.brain:
            answer = fallback_operator_answer(question, context)
            return {
                "answer": answer,
                "answered_at_ms": int(time.time() * 1000),
                "provider": "deterministic_fallback",
                "model": "local-advisory",
                "fallback_used": False,
                "fallback_from": None,
                "fallback_reason": "brain_unconfigured",
                "error_class": None,
                "advisory_mode": _ASSISTANT_ADVISORY_MODE_DEGRADED,
                "degraded_reason": "advisory_transport_unavailable",
            }

        messages = build_assistant_messages(question, context, thread_turns=thread_turns)
        primary = self.cfg.brain.get("primary")
        if primary is None:
            raise RuntimeError("assistant advisory primary provider is not configured")

        primary_failure: dict[str, Any] | None = None
        try:
            brain = self._create_assistant_brain(primary)
            resp = asyncio.run(brain.generate(messages, tools=[]))
            text = str(resp.text or "").strip()
            if not text:
                raise RuntimeError("assistant advisory response was empty")
            return {
                "answer": text,
                "answered_at_ms": int(time.time() * 1000),
                "provider": primary.type,
                "model": primary.model,
                "fallback_used": False,
                "fallback_from": None,
                "fallback_reason": None,
                "error_class": None,
                "advisory_mode": _ASSISTANT_ADVISORY_MODE_QUEUE,
                "degraded_reason": None,
            }
        except Exception as exc:
            primary_failure = {
                "provider": primary.type,
                "model": primary.model,
                "error_class": self._assistant_error_class(exc),
                "fallback_reason": self._assistant_fallback_reason(exc),
                "error": repr(exc),
            }
            if not self._assistant_should_fallback(exc):
                raise RuntimeError(
                    f"assistant advisory primary failed without fallback ({primary_failure['error_class']}): {exc}"
                ) from exc

        fallback = self.cfg.brain.get("fallback")
        if fallback is None:
            raise RuntimeError(
                f"assistant advisory fallback provider is not configured after primary failure: {primary_failure}"
            )

        try:
            brain = self._create_assistant_brain(fallback)
            resp = asyncio.run(brain.generate(messages, tools=[]))
            text = str(resp.text or "").strip()
            if not text:
                raise RuntimeError("assistant advisory fallback response was empty")
            log(
                {
                    "event": "assistant_advisory_fallback_used",
                    "request_id": Path(self.current_job_ref or "").name,
                    "primary_provider": primary_failure["provider"],
                    "primary_model": primary_failure["model"],
                    "fallback_provider": fallback.type,
                    "fallback_model": fallback.model,
                    "fallback_reason": primary_failure["fallback_reason"],
                    "error_class": primary_failure["error_class"],
                }
            )
            return {
                "answer": text,
                "answered_at_ms": int(time.time() * 1000),
                "provider": fallback.type,
                "model": fallback.model,
                "fallback_used": True,
                "fallback_from": {
                    "provider": primary_failure["provider"],
                    "model": primary_failure["model"],
                },
                "fallback_reason": primary_failure["fallback_reason"],
                "error_class": primary_failure["error_class"],
                "advisory_mode": _ASSISTANT_ADVISORY_MODE_QUEUE,
                "degraded_reason": None,
            }
        except Exception as fallback_exc:
            fallback_failure = {
                "provider": fallback.type,
                "model": fallback.model,
                "error_class": self._assistant_error_class(fallback_exc),
                "fallback_reason": self._assistant_fallback_reason(fallback_exc),
                "error": repr(fallback_exc),
            }
            raise RuntimeError(
                f"assistant advisory failed after fallback: primary={primary_failure}; fallback={fallback_failure}"
            ) from fallback_exc

    def _process_assistant_job(self, job_path: Path, payload: dict[str, Any]) -> bool:
        question = str(payload.get("question") or "").strip()
        if not question:
            raise ValueError("assistant question is required")
        thread_id = normalize_thread_id(str(payload.get("thread_id") or ""))

        self._update_job_state(
            str(job_path),
            lifecycle_state="advisory_running",
            payload={**payload, "thread_id": thread_id},
        )
        self._write_action_event(str(job_path), "assistant_job_started", thread_id=thread_id)

        context = build_operator_assistant_context(self.queue_root)
        thread_payload = read_assistant_thread(self.queue_root, thread_id)
        turns_raw = thread_payload.get("turns")
        thread_turns = (
            [item for item in turns_raw if isinstance(item, dict)]
            if isinstance(turns_raw, list)
            else []
        )
        try:
            answer_meta = self._assistant_answer_via_brain(
                question, context, thread_turns=thread_turns
            )
        except Exception as exc:
            error_text = str(exc)
            failure_artifact = {
                "schema_version": 1,
                "kind": ASSISTANT_JOB_KIND,
                "thread_id": thread_id,
                "question": question,
                "answer": "",
                "error": error_text,
                "updated_at_ms": int(time.time() * 1000),
                "advisory_mode": _ASSISTANT_ADVISORY_MODE_DEGRADED,
                "degraded_reason": "queue_processing_failed",
                "fallback_used": False,
            }
            self._assistant_response_artifact_path(str(job_path)).write_text(
                json.dumps(failure_artifact, indent=2), encoding="utf-8"
            )
            log(
                {
                    "event": "assistant_advisory_failed",
                    "request_id": job_path.name,
                    "thread_id": thread_id,
                    "error": error_text,
                    "advisory_mode": _ASSISTANT_ADVISORY_MODE_DEGRADED,
                    "degraded_reason": "queue_processing_failed",
                }
            )
            raise
        answer = str(answer_meta.get("answer") or "")

        append_thread_turn(
            self.queue_root,
            thread_id=thread_id,
            role="assistant",
            text=answer,
            request_id=job_path.name,
            ts_ms=int(time.time() * 1000),
        )

        artifact_payload = {
            "schema_version": 1,
            "kind": ASSISTANT_JOB_KIND,
            "thread_id": thread_id,
            "question": question,
            "answer": answer,
            "updated_at_ms": int(time.time() * 1000),
            "answered_at_ms": answer_meta.get("answered_at_ms"),
            "provider": answer_meta.get("provider"),
            "model": answer_meta.get("model"),
            "fallback_used": bool(answer_meta.get("fallback_used")),
            "fallback_from": answer_meta.get("fallback_from"),
            "fallback_reason": answer_meta.get("fallback_reason"),
            "error_class": answer_meta.get("error_class"),
            "advisory_mode": answer_meta.get("advisory_mode") or _ASSISTANT_ADVISORY_MODE_QUEUE,
            "degraded_reason": answer_meta.get("degraded_reason"),
            "context": context,
        }
        self._assistant_response_artifact_path(str(job_path)).write_text(
            json.dumps(artifact_payload, indent=2), encoding="utf-8"
        )

        moved = self._move_job(job_path, self.done)
        if moved is None:
            return False

        self.stats.processed += 1
        self._update_job_state(
            str(moved),
            lifecycle_state="done",
            payload={**payload, "thread_id": thread_id},
            terminal_outcome="succeeded",
        )
        self._write_action_event(str(moved), "assistant_job_done", thread_id=thread_id)
        log(
            {
                "event": "assistant_advisory_answered",
                "request_id": moved.name,
                "thread_id": thread_id,
                "provider": artifact_payload.get("provider"),
                "model": artifact_payload.get("model"),
                "fallback_used": artifact_payload.get("fallback_used"),
                "fallback_reason": artifact_payload.get("fallback_reason"),
                "error_class": artifact_payload.get("error_class"),
                "advisory_mode": artifact_payload.get("advisory_mode"),
            }
        )
        log({"event": "assistant_job_done", "job": str(moved), "thread_id": thread_id})
        return True

    def process_job_file(self, job_path: Path) -> bool:
        self.ensure_dirs()
        if not job_path.exists():
            return False
        if not self._is_primary_job_json(job_path):
            log(
                {
                    "event": "queue_metadata_ignored",
                    "path": str(job_path),
                    "reason": "not_primary_job_json",
                }
            )
            return False

        self.current_job_ref = str(job_path)
        try:
            log({"event": "queue_job_received", "job": str(job_path)})
            self._update_job_state(str(job_path), lifecycle_state="queued")
            self._update_job_state(str(job_path), lifecycle_state="planning")
            if self._shutdown_requested:
                log({"event": "queue_job_skipped_shutdown", "job": str(job_path)})
                return False

            try:
                payload = self._load_job_payload_with_retry(job_path)
                if str(payload.get("kind") or "").strip() == ASSISTANT_JOB_KIND:
                    return self._process_assistant_job(job_path, payload)
                payload = self._normalize_payload(payload)
                if self._ensure_hard_approval_gate(job_path, payload=payload):
                    return False
                mission = self._build_mission_for_payload(payload, job_ref=str(job_path))
                self._write_plan_artifact(str(job_path), payload=payload, mission=mission)
                self._update_job_state(
                    str(job_path),
                    lifecycle_state="running",
                    payload=payload,
                    mission=mission,
                    rr_data={"total_steps": len(mission.steps)},
                )
            except Exception as exc:
                log(
                    {
                        "event": "queue_job_invalid",
                        "job": str(job_path),
                        "filename": job_path.name,
                        "reason": repr(exc),
                    }
                )
                moved = self._move_job(job_path, self.failed)
                if moved is None:
                    return False
                sidecar_payload = (
                    payload if "payload" in locals() and isinstance(payload, dict) else None
                )
                self._write_failed_error_sidecar(moved, error=repr(exc), payload=sidecar_payload)
                self._update_job_state(
                    str(moved),
                    lifecycle_state="step_failed",
                    payload=sidecar_payload if isinstance(sidecar_payload, dict) else None,
                    terminal_outcome="failed",
                    failure_summary=repr(exc),
                )
                self.stats.failed += 1
                log({"event": "queue_job_failed", "job": str(moved), "error": repr(exc)})
                self.prune_failed_artifacts()
                return False

            kind = "mission_id" if payload.get("mission_id") else "goal"
            log(
                {
                    "event": "queue_job_started",
                    "kind": kind,
                    "mission": payload.get("mission_id"),
                    "goal": payload.get("goal"),
                }
            )
            rr = self.mission_runner.run(mission, context={"queue_job": str(job_path)})
            if self._shutdown_requested:
                self._finalize_job_shutdown_failure(
                    job_path, signal_reason=self._shutdown_reason, payload=payload
                )
                return False
            if rr.data.get("status") == "pending_approval":
                moved = self._move_job(job_path, self.pending)
                if moved is None:
                    return False
                self._write_pending_artifacts(
                    moved, payload=payload, mission=mission, run_data=rr.data
                )
                self._update_job_state(
                    str(moved),
                    lifecycle_state="awaiting_approval",
                    payload=payload,
                    mission=mission,
                    rr_data=rr.data,
                    approval_status="pending",
                )
                self._write_action_event(
                    str(moved), "queue_job_pending_approval", step=rr.data.get("step")
                )
                log(
                    {
                        "event": "queue_job_pending_approval",
                        "job": str(moved),
                        "step": rr.data.get("step"),
                        "reason": rr.data.get("reason"),
                    }
                )
                return False

            if not rr.ok:
                moved = self._move_job(job_path, self.failed)
                if moved is None:
                    return False
                error_text = rr.error or "mission failed"
                self._write_failed_error_sidecar(moved, error=error_text, payload=payload)
                self._update_job_state(
                    str(moved),
                    lifecycle_state=str(rr.data.get("lifecycle_state") or "step_failed"),
                    payload=payload,
                    mission=mission,
                    rr_data=rr.data,
                    terminal_outcome=str(rr.data.get("terminal_outcome") or "failed"),
                    failure_summary=error_text,
                    blocked_reason=error_text
                    if str(rr.data.get("terminal_outcome") or "") == "blocked"
                    else None,
                )
                self.stats.failed += 1
                log({"event": "queue_job_failed", "job": str(moved), "error": error_text})
                self._write_action_event(str(moved), "queue_job_failed", error=error_text)
                self.prune_failed_artifacts()
                return False

            moved = self._move_job(job_path, self.done)
            if moved is None:
                return False
            self.stats.processed += 1
            self._write_run_streams(str(moved), rr.data)
            self._update_job_state(
                str(moved),
                lifecycle_state="done",
                payload=payload,
                mission=mission,
                rr_data=rr.data,
                terminal_outcome=str(rr.data.get("terminal_outcome") or "succeeded"),
                approval_status="approved" if rr.data.get("step_outcomes") else None,
            )
            self._write_action_event(str(moved), "queue_job_done")
            log({"event": "queue_job_done", "job": str(moved)})
            record_mission_success(self.queue_root)
            return True
        finally:
            self.current_job_ref = None

    def _approval_ref_candidates(self, ref: str) -> list[str]:
        base = Path(ref.strip()).name
        if base.endswith(".approval.json"):
            base = f"{base.removesuffix('.approval.json')}.json"

        candidates = [
            base,
            base.replace(".pending.json", ".json"),
            base.replace(".json", ".pending.json"),
        ]
        if "." not in base:
            candidates.extend([f"{base}.json", f"{base}.pending.json"])

        stem = Path(base).stem.removesuffix(".approval")
        short = stem.removeprefix("job-")
        candidates.extend([f"{stem}.json", f"job-{short}.json", f"{short}.json"])

        ordered: list[str] = []
        for cand in candidates:
            if cand and cand not in ordered:
                ordered.append(cand)
        return ordered

    def canonicalize_approval_ref(self, ref: str) -> str:
        job, _meta, _artifact = self._resolve_pending_approval_paths(ref)
        return job.name

    def _resolve_pending_approval_paths(self, ref: str) -> tuple[Path, Path, Path]:
        raw_ref = ref.strip()
        if not raw_ref:
            raise FileNotFoundError("pending job not found: (empty ref)")

        for candidate_name in self._approval_ref_candidates(raw_ref):
            candidate_path = self.pending / candidate_name
            if not candidate_path.exists() or not candidate_path.is_file():
                continue

            if candidate_name.endswith(".pending.json"):
                stem = candidate_name.removesuffix(".pending.json")
                canonical_job = self.pending / f"{stem}.json"
                job = canonical_job if canonical_job.exists() else candidate_path
                meta = self.pending / f"{stem}.pending.json"
                artifact = self.approvals / f"{stem}.approval.json"
                return job, meta, artifact

            stem = Path(candidate_name).stem
            job = candidate_path
            meta = self.pending / f"{stem}.pending.json"
            artifact = self.approvals / f"{stem}.approval.json"
            return job, meta, artifact

        # Fallback via approval artifact aliases.
        for artifact in self._iter_approval_artifacts():
            if Path(raw_ref).name not in self._approval_ref_variants(artifact):
                continue
            stem = artifact.stem.removesuffix(".approval")
            job = self.pending / f"{stem}.json"
            meta = self.pending / f"{stem}.pending.json"
            if job.exists() and job.is_file():
                return job, meta, self.approvals / f"{stem}.approval.json"

        raise FileNotFoundError(f"pending job not found: {ref}")

    def approvals_list(self) -> list[dict[str, Any]]:
        self.ensure_dirs()
        out: list[dict[str, Any]] = []
        for artifact in self._iter_approval_artifacts():
            out.append(self._read_approval_artifact(artifact))
        return out

    def resolve_approval(self, ref: str, *, approve: bool, approve_always: bool = False) -> bool:
        self.ensure_dirs()
        job, meta_path, artifact_path = self._resolve_pending_approval_paths(ref)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            payload: dict[str, Any] = {}
            try:
                loaded = json.loads(job.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = {}
            meta = {
                "status": "pending_approval",
                "job": job.name,
                "payload": payload,
                "resume_step": 1,
                "mission": {},
            }
        if not approve:
            moved = self._move_job(job, self.failed)
            if moved is None:
                meta_path.unlink(missing_ok=True)
                artifact_path.unlink(missing_ok=True)
                return False
            self._write_failed_error_sidecar(
                moved,
                error="Denied in approval inbox",
                payload=meta.get("payload") if isinstance(meta, dict) else None,
            )
            self.stats.failed += 1
            mission_data = meta.get("mission", {})
            denied_mission = MissionTemplate(
                id=mission_data.get("id", "queue_mission"),
                title=mission_data.get("title", "Queued Mission"),
                goal=mission_data.get("goal", ""),
                notes=mission_data.get("notes"),
                steps=[],
            )
            self._update_job_state(
                str(moved),
                lifecycle_state="blocked",
                payload=meta.get("payload") if isinstance(meta, dict) else None,
                mission=denied_mission,
                rr_data={
                    "current_step_index": int(meta.get("resume_step", 1) or 1),
                    "last_attempted_step": int(meta.get("resume_step", 1) or 1),
                },
                terminal_outcome="denied",
                failure_summary="Denied in approval inbox",
                blocked_reason="approval denied by operator",
                approval_status="denied",
            )
            self.mission_runner._append_mission_log(denied_mission, [], status="denied")
            log(
                {
                    "event": "mission_denied",
                    "mission": meta.get("mission", {}).get("id"),
                    "reason": "approval denied from inbox",
                }
            )
            log(
                {
                    "event": "queue_job_failed",
                    "job": str(moved),
                    "error": "Denied in approval inbox",
                }
            )
            self._write_action_event(
                str(moved), "queue_job_failed", error="Denied in approval inbox"
            )
            self.prune_failed_artifacts()
            meta_path.unlink(missing_ok=True)
            artifact_path.unlink(missing_ok=True)
            return True

        payload = meta.get("payload", {})
        approval_data: dict[str, Any] = {}
        if artifact_path.exists():
            approval_data = self._read_approval_artifact(artifact_path)
        if approve_always and approval_data:
            self.grant_approval_scope(
                skill=str(approval_data.get("skill", "")),
                capability=str(approval_data.get("capability", "unknown")),
                scope=approval_data.get("scope", {}),
            )
        mission_data = meta.get("mission", {})
        steps_raw = mission_data.get("steps", []) if isinstance(mission_data, dict) else []
        if isinstance(steps_raw, list) and steps_raw:
            steps = [
                MissionStep(skill_id=item["skill_id"], args=item.get("args", {}))
                for item in steps_raw
            ]
            mission = MissionTemplate(
                id=mission_data.get("id", payload.get("mission_id", "queue_mission")),
                title=mission_data.get("title", "Queued Mission"),
                goal=mission_data.get("goal", payload.get("goal", "")),
                notes=mission_data.get("notes"),
                steps=steps,
            )
            resume_step = int(meta.get("resume_step", 1) or 1)
        else:
            source_payload = payload if isinstance(payload, dict) else {}
            mission = self._build_mission_for_payload(source_payload, job_ref=str(job))
            resume_step = 1
        self.current_job_ref = str(job)
        try:
            resume_skill = (
                mission.steps[max(resume_step - 1, 0)].skill_id
                if mission.steps and max(resume_step - 1, 0) < len(mission.steps)
                else ""
            )
            self._approved_steps.add((str(job), resume_step, resume_skill))
            self._update_job_state(
                str(job),
                lifecycle_state="resumed",
                payload=payload if isinstance(payload, dict) else None,
                mission=mission,
                rr_data={
                    "current_step_index": max(resume_step - 1, 0),
                    "last_completed_step": max(resume_step - 1, 0),
                    "last_attempted_step": resume_step,
                    "total_steps": len(mission.steps),
                },
                approval_status="approved",
            )
            self._update_job_state(
                str(job),
                lifecycle_state="running",
                payload=payload if isinstance(payload, dict) else None,
                mission=mission,
                rr_data={"total_steps": len(mission.steps)},
                approval_status="approved",
            )
            rr = self.mission_runner.run(
                mission,
                start_step=resume_step,
                context={"queue_job": str(job), "approval_resumed": True},
            )
            if rr.data.get("status") == "pending_approval":
                self._write_pending_artifacts(
                    job, payload=payload, mission=mission, run_data=rr.data
                )
                self._update_job_state(
                    str(job),
                    lifecycle_state="awaiting_approval",
                    payload=payload if isinstance(payload, dict) else None,
                    mission=mission,
                    rr_data=rr.data,
                    terminal_outcome=None,
                    approval_status="pending",
                )
                log(
                    {
                        "event": "queue_job_pending_approval",
                        "job": str(job),
                        "step": rr.data.get("step"),
                        "reason": rr.data.get("reason"),
                    }
                )
                self._write_action_event(
                    str(job), "queue_job_pending_approval", step=rr.data.get("step")
                )
                return False
            if not rr.ok:
                moved = self._move_job(job, self.failed)
                if moved is None:
                    meta_path.unlink(missing_ok=True)
                    artifact_path.unlink(missing_ok=True)
                    return False
                error_text = rr.error or "mission failed"
                self._write_failed_error_sidecar(
                    moved, error=error_text, payload=payload if isinstance(payload, dict) else None
                )
                self._update_job_state(
                    str(moved),
                    lifecycle_state=str(rr.data.get("lifecycle_state") or "step_failed"),
                    payload=payload if isinstance(payload, dict) else None,
                    mission=mission,
                    rr_data=rr.data,
                    terminal_outcome=str(rr.data.get("terminal_outcome") or "failed"),
                    failure_summary=error_text,
                    blocked_reason=error_text
                    if str(rr.data.get("terminal_outcome") or "") == "blocked"
                    else None,
                    approval_status="approved",
                )
                self.stats.failed += 1
                log({"event": "queue_job_failed", "job": str(moved), "error": error_text})
                self._write_run_streams(str(moved), rr.data)
                self._write_action_event(str(moved), "queue_job_failed", error=error_text)
                self.prune_failed_artifacts()
                meta_path.unlink(missing_ok=True)
                artifact_path.unlink(missing_ok=True)
                return False

            moved = self._move_job(job, self.done)
            if moved is None:
                meta_path.unlink(missing_ok=True)
                artifact_path.unlink(missing_ok=True)
                return False
            self.stats.processed += 1
            self._write_run_streams(str(moved), rr.data)
            self._update_job_state(
                str(moved),
                lifecycle_state="done",
                payload=payload if isinstance(payload, dict) else None,
                mission=mission,
                rr_data=rr.data,
                terminal_outcome=str(rr.data.get("terminal_outcome") or "succeeded"),
                approval_status="approved",
            )
            self._write_action_event(str(moved), "queue_job_done", via="approval_inbox")
            log({"event": "queue_job_done", "job": str(moved), "via": "approval_inbox"})
            record_mission_success(self.queue_root)
            meta_path.unlink(missing_ok=True)
            artifact_path.unlink(missing_ok=True)
            return True
        finally:
            self.current_job_ref = None

    def process_pending_once(self) -> int:
        self.ensure_dirs()
        self._update_daemon_health_state(last_tick_ts_ms=int(time.time() * 1000))
        record_health_ok(self.queue_root, "daemon_tick")
        self._auto_relocate_legacy_jobs()
        self._auto_relocate_misplaced_pending_jobs()
        if self._shutdown_requested:
            log({"event": "queue_daemon_intake_stopped", "reason": self._shutdown_reason})
            return 0
        if self.is_paused():
            log({"event": "queue_tick_paused", "queue": str(self.inbox)})
            return 0
        processed = 0
        for job in sorted(self.inbox.glob("*.json")):
            if self._shutdown_requested:
                log({"event": "queue_daemon_intake_stopped", "reason": self._shutdown_reason})
                break
            if not self._is_ready_job_file(job):
                continue
            self.process_job_file(job)
            processed += 1
        return processed

    def _config_snapshot_path(self) -> Path:
        return self.queue_root / "_ops" / "config_snapshot.json"

    def _config_last_snapshot_path(self) -> Path:
        return self.queue_root / "_ops" / "config_snapshot.last.json"

    def _config_last_fingerprint_path(self) -> Path:
        return self.queue_root / "_ops" / "config_snapshot.last.sha256"

    def _config_drift_note_path(self) -> Path:
        return self.queue_root / "config_drift_note.txt"

    def _write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _snapshot_and_check_config_drift(self) -> None:
        snapshot_path = write_config_snapshot(
            self.queue_root, self.settings, filename="_ops/config_snapshot.json"
        )
        fingerprint_path = write_config_fingerprint(self.queue_root, self.settings)
        current_hash = fingerprint_path.read_text(encoding="utf-8").strip()

        current_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        current_settings = (
            current_payload.get("settings") if isinstance(current_payload, dict) else {}
        )
        if not isinstance(current_settings, dict):
            current_settings = {}

        last_payload_path = self._config_last_snapshot_path()
        old_settings: dict[str, Any] = {}
        if last_payload_path.exists():
            try:
                old_payload = json.loads(last_payload_path.read_text(encoding="utf-8"))
            except Exception:
                old_payload = {}
            raw = old_payload.get("settings") if isinstance(old_payload, dict) else {}
            if isinstance(raw, dict):
                old_settings = raw

        changed_keys = sorted(
            key
            for key in set(old_settings.keys()) | set(current_settings.keys())
            if old_settings.get(key) != current_settings.get(key)
        )

        baseline_path = self._config_last_fingerprint_path()
        baseline_hash = (
            baseline_path.read_text(encoding="utf-8").strip() if baseline_path.exists() else ""
        )
        if baseline_hash:
            if baseline_hash != current_hash:
                ts_ms = int(time.time() * 1000)
                log(
                    {
                        "event": "config_drift_detected",
                        "changed": True,
                        "changed_keys": changed_keys,
                        "old_hash": baseline_hash,
                        "new_hash": current_hash,
                        "snapshot_path": str(snapshot_path.resolve()),
                        "queue_root": str(self.queue_root.resolve()),
                        "daemon_pid": os.getpid(),
                    }
                )
                note = (
                    f"config drift detected\n"
                    f"ts_ms={ts_ms}\n"
                    f"queue_root={self.queue_root.resolve()}\n"
                    f"snapshot_path={snapshot_path.resolve()}\n"
                    f"old_hash={baseline_hash}\n"
                    f"new_hash={current_hash}\n"
                    f"changed_keys={','.join(changed_keys)}\n"
                )
                self._write_text_atomic(self._config_drift_note_path(), note)
            self._write_text_atomic(baseline_path, current_hash + "\n")
        else:
            self._write_text_atomic(baseline_path, current_hash + "\n")

        last_payload = dict(current_payload) if isinstance(current_payload, dict) else {}
        last_payload["snapshot_hash"] = current_hash
        self._write_text_atomic(
            last_payload_path, json.dumps(last_payload, indent=2, sort_keys=True) + "\n"
        )

    def run(self, once: bool = False) -> None:
        self.ensure_dirs()
        self._shutdown_requested = False
        self._shutdown_reason = None
        now_ms = int(time.time() * 1000)
        self._update_daemon_health_state(
            daemon_started_at_ms=now_ms,
            daemon_pid=os.getpid(),
            shutdown_requested=False,
        )
        self._acquire_daemon_lock()
        self.recover_on_startup()
        self._snapshot_and_check_config_drift()

        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _handle_shutdown(signum, _frame):
            reason = signal.Signals(signum).name
            self.request_shutdown(reason)

        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)
        if self.auto_approve_ask and not self.dev_mode:
            log(
                {"event": "queue_auto_approve_disabled", "reason": "VOXERA_DEV_MODE is not enabled"}
            )
        log(
            {
                "event": "queue_daemon_start",
                "queue": str(self.inbox),
                "once": once,
                "auto_approve_ask": self.auto_approve_ask,
            }
        )
        try:
            if once:
                count = self.process_pending_once()
                log({"event": "queue_daemon_stop", "reason": "once", "processed": count})
                return

            try:
                from watchdog.events import FileSystemEventHandler
                from watchdog.observers import Observer

                daemon = self

                class _Handler(FileSystemEventHandler):
                    def on_created(self, event):
                        if event.is_directory:
                            return
                        path = Path(event.src_path)
                        if daemon._shutdown_requested:
                            return
                        if daemon._is_ready_job_file(path):
                            daemon.process_job_file(path)

                observer = Observer()
                observer.schedule(_Handler(), str(self.inbox), recursive=False)
                observer.start()
                log({"event": "queue_watch_mode", "mode": "watchdog"})
                self.process_pending_once()
                try:
                    while not self._shutdown_requested:
                        time.sleep(self.poll_interval)
                finally:
                    observer.stop()
                    observer.join()
            except ImportError:
                log({"event": "queue_watch_mode", "mode": "poll"})
                while not self._shutdown_requested:
                    self.process_pending_once()
                    if self._shutdown_requested:
                        break
                    time.sleep(self.poll_interval)
        except Exception as exc:
            self._record_failed_shutdown(exc)
            record_health_error(self.queue_root, f"daemon run error: {exc}")
            raise
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
            self.release_daemon_lock()
            if self._shutdown_requested:
                self._record_clean_shutdown(self._shutdown_reason or "graceful_stop")
            elif once:
                self._record_clean_shutdown("graceful_stop")
