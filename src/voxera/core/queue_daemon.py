from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess  # noqa: F401
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit import log, tail
from ..config import load_app_config as load_config
from ..config import load_config as load_runtime_config
from ..config import write_config_fingerprint, write_config_snapshot
from ..health import (
    increment_health_counter,
    read_health_snapshot,
    record_health_error,
    record_health_ok,
    update_health_snapshot,
)
from ..paths import queue_root as default_queue_root
from ..skills.registry import SkillRegistry
from ..skills.runner import SkillRunner
from . import queue_assistant
from .mission_planner import plan_mission as plan_mission  # re-export for monkeypatch compatibility
from .missions import MissionRunner, MissionTemplate
from .queue_approvals import QueueApprovalMixin
from .queue_execution import QueueExecutionMixin
from .queue_paths import move_job_with_sidecar
from .queue_recovery import QueueRecoveryMixin
from .queue_state import (
    read_job_state,
    update_job_state_snapshot,
    write_job_state,
)

_PARSE_RETRY_ATTEMPTS = 4
_PARSE_RETRY_BACKOFF_S = 0.1

_FAILED_SIDECAR_SCHEMA_WRITE_VERSION = 1
_FAILED_SIDECAR_SCHEMA_READ_VERSIONS = {_FAILED_SIDECAR_SCHEMA_WRITE_VERSION}
_FAILED_TIMESTAMP_MS_MIN = 10**12


class QueueLockError(RuntimeError):
    pass


@dataclass
class QueueStats:
    processed: int = 0
    failed: int = 0


class MissionQueueDaemon(QueueApprovalMixin, QueueRecoveryMixin, QueueExecutionMixin):
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

    def _redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.privacy.redact_logs:
            return args
        return {k: "<redacted>" for k in args}

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

    def _read_job_state(self, job_ref: str) -> dict[str, Any]:
        return read_job_state(
            job_ref,
            inbox=self.inbox,
            pending=self.pending,
            done=self.done,
            failed=self.failed,
            canceled=self.canceled,
        )

    def _write_job_state(self, job_ref: str, payload: dict[str, Any]) -> None:
        write_job_state(
            job_ref,
            payload,
            inbox=self.inbox,
            pending=self.pending,
            done=self.done,
            failed=self.failed,
            canceled=self.canceled,
            write_text_atomic=self._write_text_atomic,
        )

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
        snapshot = update_job_state_snapshot(
            job_ref,
            lifecycle_state=lifecycle_state,
            current=current,
            now_ms=now_ms,
            payload=payload,
            mission=mission,
            rr_data=rr_data,
            terminal_outcome=terminal_outcome,
            failure_summary=failure_summary,
            blocked_reason=blocked_reason,
            approval_status=approval_status,
        )
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
        def _on_already_moved(missing_src: Path, move_target_dir: Path) -> None:
            log(
                {
                    "event": "queue_job_already_moved",
                    "job": str(missing_src),
                    "target_dir": str(move_target_dir),
                }
            )

        return move_job_with_sidecar(src, target_dir, on_already_moved=_on_already_moved)

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

    def _is_snapshot_artifact(self, path: Path) -> bool:
        name = path.name
        if not name.endswith(".json"):
            return False
        if name == "config_snapshot.json":
            return True
        if name.startswith("config_snapshot"):
            return True
        return "_ops" in path.parts

    def _count_files(self, directory: Path, pattern: str) -> int:
        if not directory.exists():
            return 0
        return sum(1 for _ in directory.glob(pattern))

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

    def _assistant_response_artifact_path(self, job_ref: str) -> Path:
        return queue_assistant.assistant_response_artifact_path(self, job_ref)

    def _create_assistant_brain(self, provider: Any):
        return queue_assistant.create_assistant_brain(provider)

    def _assistant_brain_candidates(self) -> list[tuple[str, Any]]:
        return queue_assistant.assistant_brain_candidates(self.cfg)

    def _assistant_answer_via_brain(
        self, question: str, context: dict[str, Any], *, thread_turns: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return queue_assistant.assistant_answer_via_brain(
            question,
            context,
            thread_turns=thread_turns,
            attempts=self._assistant_brain_candidates(),
            create_brain=self._create_assistant_brain,
        )

    def _process_assistant_job(self, job_path: Path, payload: dict[str, Any]) -> bool:
        return queue_assistant.process_assistant_job(self, job_path, payload)

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
