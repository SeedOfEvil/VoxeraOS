from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..health import read_health_snapshot, record_last_shutdown
from .queue_paths import deterministic_target_path


def _queue_daemon_module() -> Any:
    from . import queue_daemon

    return queue_daemon


def _log(event: dict[str, Any]) -> None:
    _queue_daemon_module().log(event)


_STARTUP_RECOVERY_REASON = "recovered_after_restart"
_STARTUP_RECOVERY_MESSAGE = (
    "daemon recovered from unclean shutdown; job marked failed deterministically"
)
_SHUTDOWN_REASON_MAX_LEN = 240


def shutdown_reason_excerpt(reason: str) -> str:
    return reason[:_SHUTDOWN_REASON_MAX_LEN]


class QueueRecoveryMixin:
    current_job_ref: Any
    _shutdown_reason: Any

    def request_shutdown(self: Any, reason: str) -> None:
        if self._shutdown_requested:
            return
        normalized = reason.upper()
        self._shutdown_requested = True
        self._shutdown_reason = normalized
        self._update_daemon_health_state(shutdown_requested=True)
        _log(
            {
                "event": "queue_daemon_shutdown_requested",
                "reason": normalized,
                "job": self.current_job_ref,
                "ts_ms": int(time.time() * 1000),
            }
        )

    def _record_clean_shutdown(self: Any, reason: str) -> None:
        snapshot = read_health_snapshot(self.queue_root)
        if snapshot.get("last_shutdown_outcome") == "failed_shutdown":
            return
        record_last_shutdown(
            self.queue_root,
            outcome="clean",
            reason=reason,
            job=self.current_job_ref,
        )

    def _record_failed_shutdown(self: Any, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {str(exc).strip()}"
        record_last_shutdown(
            self.queue_root,
            outcome="failed_shutdown",
            reason=shutdown_reason_excerpt(reason),
            job=self.current_job_ref,
        )

    def _deterministic_target_path(
        self: Any,
        target_dir: Path,
        file_name: str,
        *,
        suffix_tag: str,
    ) -> Path:
        return deterministic_target_path(target_dir, file_name, suffix_tag=suffix_tag)

    def _safe_relative_to_queue(self: Any, entry: Path) -> Path:
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

    def _quarantine_startup_recovery_path(self: Any, src: Path, recovery_root: Path) -> Path:
        relative = self._safe_relative_to_queue(src)
        destination = recovery_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(destination))
        return destination

    def _detected_inflight_pending_jobs(self: Any) -> list[dict[str, Any]]:
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

    def _collect_orphan_approval_files(self: Any) -> list[Path]:
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

    def _collect_orphan_state_files(self: Any) -> list[Path]:
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

    def recover_on_startup(self: Any, *, now_ms: int | None = None) -> dict[str, Any]:
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
        _log(
            {
                "event": "daemon_startup_recovery",
                "ts_ms": recovery_ts_ms,
                "policy": "fail_fast",
                "reason": _STARTUP_RECOVERY_REASON,
                "message": _STARTUP_RECOVERY_MESSAGE,
                "counts": counts,
                "jobs_failed": report["jobs_failed"],
                "quarantined_paths": report["quarantined_paths"],
                "recovery_dir": report["recovery_dir"],
                "summary": summary,
            }
        )
        if counts["jobs_failed"]:
            record_last_shutdown(
                self.queue_root,
                outcome="startup_recovered",
                reason=_STARTUP_RECOVERY_REASON,
                job=failed_jobs[-1],
            )

        return report

    def _shutdown_failure_payload(self: Any) -> dict[str, Any]:
        return {
            "reason": "shutdown",
            "message": "daemon shutdown requested",
            "signal": self._shutdown_reason or "UNKNOWN",
        }

    def _finalize_job_shutdown_failure(
        self: Any,
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
        _log(
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
