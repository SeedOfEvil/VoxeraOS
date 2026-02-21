from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit import log, tail
from ..config import load_config
from ..skills.registry import SkillRegistry
from ..skills.runner import SkillRunner
from .mission_planner import MissionPlannerError, plan_mission
from .missions import MissionRunner, MissionStep, MissionTemplate, get_mission

_AUTO_APPROVE_ALLOWLIST = {"system.settings"}
_PARSE_RETRY_ATTEMPTS = 4
_PARSE_RETRY_BACKOFF_S = 0.1
_FAILED_SIDECAR_SCHEMA_WRITE_VERSION = 1
_FAILED_SIDECAR_SCHEMA_READ_VERSIONS = {_FAILED_SIDECAR_SCHEMA_WRITE_VERSION}
_FAILED_TIMESTAMP_MS_MIN = 10**12


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
        self.queue_root = (
            queue_root or (Path.home() / "VoxeraOS" / "notes" / "queue")
        ).expanduser()
        self.inbox = self.queue_root
        self.done = self.queue_root / "done"
        self.failed = self.queue_root / "failed"
        self.pending = self.queue_root / "pending"
        self.approvals = self.pending / "approvals"
        self.poll_interval = poll_interval
        self.stats = QueueStats()
        self.current_job_ref: str | None = None
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
        self.dev_mode = os.getenv("VOXERA_DEV_MODE") == "1"
        self.failed_retention_max_age_s = (
            failed_retention_max_age_s
            if failed_retention_max_age_s is not None
            else self._env_float("VOXERA_QUEUE_FAILED_MAX_AGE_S")
        )
        self.failed_retention_max_count = (
            failed_retention_max_count
            if failed_retention_max_count is not None
            else self._env_int("VOXERA_QUEUE_FAILED_MAX_COUNT")
        )

    def _env_float(self, key: str) -> float | None:
        raw = os.getenv(key)
        if not raw:
            return None
        try:
            value = float(raw)
            return value if value > 0 else None
        except ValueError:
            return None

    def _env_int(self, key: str) -> int | None:
        raw = os.getenv(key)
        if not raw:
            return None
        try:
            value = int(raw)
            return value if value > 0 else None
        except ValueError:
            return None

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

        approval_key = (self.current_job_ref or "", int(step or 0), manifest.id)
        if approval_key in self._approved_steps:
            self._approved_steps.discard(approval_key)
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
            }
        )
        return {
            "status": "pending",
            "step": step,
            "skill": manifest.id,
            "reason": reason,
            "capability": capability,
            "args": self._redact_args(args or {}),
        }

    def ensure_dirs(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.done.mkdir(parents=True, exist_ok=True)
        self.failed.mkdir(parents=True, exist_ok=True)
        self.pending.mkdir(parents=True, exist_ok=True)
        self.approvals.mkdir(parents=True, exist_ok=True)

    def _move_job(self, src: Path, target_dir: Path) -> Path:
        target = target_dir / src.name
        if target.exists():
            ts = int(time.time() * 1000)
            target = target_dir / f"{src.stem}-{ts}{src.suffix}"
        shutil.move(str(src), str(target))
        return target

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
        self._failed_error_sidecar(failed_job).write_text(
            json.dumps(details, indent=2), encoding="utf-8"
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

        removed_jobs = 0
        removed_sidecars = 0
        for unit in ordered:
            if unit["key"] in keep_keys:
                continue
            job = unit.get("job")
            sidecar = unit.get("sidecar")
            if job is not None and job.exists():
                job.unlink()
                removed_jobs += 1
            if sidecar is not None and sidecar.exists():
                sidecar.unlink()
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
        return normalized

    def _build_mission_for_payload(
        self, payload: dict[str, Any], *, job_ref: str
    ) -> MissionTemplate:
        normalized = self._normalize_payload(payload)
        if "mission_id" in normalized:
            return get_mission(normalized["mission_id"])
        if "goal" in normalized:
            try:
                return asyncio.run(
                    plan_mission(
                        goal=normalized["goal"],
                        cfg=self.cfg,
                        registry=self.mission_runner.skill_runner.registry,
                        source="queue",
                        job_ref=job_ref,
                    )
                )
            except MissionPlannerError as exc:
                raise RuntimeError(str(exc)) from exc
        raise ValueError("job must contain either mission_id (or mission) or goal (or plan_goal)")

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
            "capability": run_data.get("capability"),
            "status": "pending_approval",
            "ts": time.time(),
        }
        artifact_path = self.approvals / f"{job_in_pending.stem}.approval.json"
        artifact_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
        self._notify_pending_approval(approval)

        meta = {
            "status": "pending_approval",
            "job": job_in_pending.name,
            "payload": payload,
            "resume_step": step,
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

    def _notify_pending_approval(self, approval: dict[str, Any]) -> None:
        if os.getenv("VOXERA_NOTIFY") != "1":
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

    def _is_ready_job_file(self, path: Path) -> bool:
        if path.parent != self.inbox or not path.is_file():
            return False
        name = path.name
        if not name.endswith(".json"):
            return False
        if name.startswith("."):
            return False
        blocked_suffixes = (".pending.json", ".approval.json", ".tmp.json", ".partial.json")
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

    def _count_files(self, directory: Path, pattern: str) -> int:
        if not directory.exists():
            return 0
        return sum(1 for _ in directory.glob(pattern))

    def _is_primary_job_json(self, path: Path) -> bool:
        return path.name.endswith(".json") and not path.name.endswith(
            (".pending.json", ".approval.json", ".error.json")
        )

    def _pending_primary_jobs(self) -> list[Path]:
        if not self.pending.exists():
            return []
        return sorted(
            p
            for p in self.pending.glob("*.json")
            if p.is_file() and not p.name.endswith(".pending.json")
        )

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

    def _read_approval_artifact(self, artifact: Path) -> dict[str, Any]:
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("approval artifact must be a JSON object")
            data["_artifact"] = artifact.name
            data["job"] = self._canonical_job_name(artifact, data)
            data["approve_refs"] = [
                data["job"],
                Path(data["job"]).stem.removeprefix("job-"),
                str((self.pending / data["job"]).resolve()),
            ]
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
                    "capability": data.get("capability", ""),
                }
            )
        return out

    def recent_failed_jobs_snapshot(
        self, *, limit: int = 10, audit_tail: int = 200
    ) -> list[dict[str, Any]]:
        if not self.failed.exists():
            return []

        error_by_job: dict[str, str] = {}
        for event in reversed(tail(audit_tail)):
            if event.get("event") != "queue_job_failed":
                continue
            job = Path(str(event.get("job", ""))).name
            if not job or job in error_by_job:
                continue
            error_by_job[job] = str(event.get("error") or "")

        files = sorted(
            (p for p in self.failed.glob("*.json") if self._is_primary_job_json(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        rows: list[dict[str, Any]] = []
        for item in files[:limit]:
            sidecar_error = ""
            sidecar = self._read_failed_error_sidecar(item)
            if sidecar is not None:
                sidecar_error = str(sidecar.get("error") or "")
            rows.append(
                {"job": item.name, "error": sidecar_error or error_by_job.get(item.name, "")}
            )
        return rows

    def _failed_sidecar_health_snapshot(self) -> dict[str, int]:
        if not self.failed.exists():
            return {
                "failed_sidecars_valid": 0,
                "failed_sidecars_invalid": 0,
                "failed_sidecars_missing": 0,
            }

        valid = 0
        invalid = 0
        missing = 0
        for failed_job in self.failed.glob("*.json"):
            if not self._is_primary_job_json(failed_job):
                continue
            sidecar = self._failed_error_sidecar(failed_job)
            if not sidecar.exists():
                missing += 1
                continue
            payload = self._read_failed_error_sidecar(failed_job)
            if payload is None:
                invalid += 1
            else:
                valid += 1
        return {
            "failed_sidecars_valid": valid,
            "failed_sidecars_invalid": invalid,
            "failed_sidecars_missing": missing,
        }

    def status_snapshot(
        self, *, approvals_limit: int = 10, failed_limit: int = 10
    ) -> dict[str, Any]:
        sidecar_health = self._failed_sidecar_health_snapshot()
        return {
            "queue_root": str(self.queue_root),
            "exists": self.queue_root.exists(),
            "counts": {
                "pending": len(self._pending_primary_jobs()),
                "pending_approvals": self._count_files(self.approvals, "*.approval.json"),
                "done": self._count_files(self.done, "*.json"),
                "failed": sum(1 for p in self.failed.glob("*.json") if self._is_primary_job_json(p))
                if self.failed.exists()
                else 0,
            },
            **sidecar_health,
            "pending_approvals": self.pending_approvals_snapshot(limit=approvals_limit),
            "recent_failed": self.recent_failed_jobs_snapshot(limit=failed_limit),
        }

    def process_job_file(self, job_path: Path) -> bool:
        self.ensure_dirs()
        if not job_path.exists():
            return False

        self.current_job_ref = str(job_path)
        log({"event": "queue_job_received", "job": str(job_path)})
        try:
            payload = self._load_job_payload_with_retry(job_path)
            payload = self._normalize_payload(payload)
            mission = self._build_mission_for_payload(payload, job_ref=str(job_path))
        except Exception as exc:
            moved = self._move_job(job_path, self.failed)
            sidecar_payload = (
                payload if "payload" in locals() and isinstance(payload, dict) else None
            )
            self._write_failed_error_sidecar(moved, error=repr(exc), payload=sidecar_payload)
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
        if rr.data.get("status") == "pending_approval":
            moved = self._move_job(job_path, self.pending)
            self._write_pending_artifacts(moved, payload=payload, mission=mission, run_data=rr.data)
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
            error_text = rr.error or "mission failed"
            self._write_failed_error_sidecar(moved, error=error_text, payload=payload)
            self.stats.failed += 1
            log({"event": "queue_job_failed", "job": str(moved), "error": error_text})
            self.prune_failed_artifacts()
            return False

        moved = self._move_job(job_path, self.done)
        self.stats.processed += 1
        log({"event": "queue_job_done", "job": str(moved)})
        return True

    def _find_pending_job(self, ref: str) -> Path:
        raw_ref = ref.strip()
        if not raw_ref:
            raise FileNotFoundError("pending job not found: (empty ref)")

        direct = Path(raw_ref).expanduser()
        if direct.exists() and direct.is_file():
            if direct.parent == self.pending:
                return direct
            if direct.parent == self.approvals:
                pending_from_approval = (
                    self.pending / f"{direct.stem.removesuffix('.approval')}.json"
                )
                if pending_from_approval.exists():
                    return pending_from_approval

        base = Path(raw_ref).name
        stem = Path(base).stem.removesuffix(".approval")
        short = stem.removeprefix("job-")
        for cand in {
            self.pending / base,
            self.pending / f"{stem}.json",
            self.pending / f"job-{short}.json",
            self.pending / f"{short}.json",
        }:
            if cand.exists() and cand.is_file():
                return cand

        target = base
        for artifact in self._iter_approval_artifacts():
            if target in self._approval_ref_variants(artifact):
                via_artifact = self.pending / f"{artifact.stem.removesuffix('.approval')}.json"
                if via_artifact.exists():
                    return via_artifact

        raise FileNotFoundError(f"pending job not found: {ref}")

    def approvals_list(self) -> list[dict[str, Any]]:
        self.ensure_dirs()
        out: list[dict[str, Any]] = []
        for artifact in self._iter_approval_artifacts():
            out.append(self._read_approval_artifact(artifact))
        return out

    def resolve_approval(self, ref: str, *, approve: bool) -> bool:
        self.ensure_dirs()
        job = self._find_pending_job(ref)
        meta_path = self.pending / f"{job.stem}.pending.json"
        artifact_path = self.approvals / f"{job.stem}.approval.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"approval metadata missing for {job.name}")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not approve:
            moved = self._move_job(job, self.failed)
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
            self.prune_failed_artifacts()
            meta_path.unlink(missing_ok=True)
            artifact_path.unlink(missing_ok=True)
            return True

        payload = meta.get("payload", {})
        mission_data = meta.get("mission", {})
        steps = [
            MissionStep(skill_id=item["skill_id"], args=item.get("args", {}))
            for item in mission_data.get("steps", [])
        ]
        mission = MissionTemplate(
            id=mission_data.get("id", payload.get("mission_id", "queue_mission")),
            title=mission_data.get("title", "Queued Mission"),
            goal=mission_data.get("goal", payload.get("goal", "")),
            notes=mission_data.get("notes"),
            steps=steps,
        )
        self.current_job_ref = str(job)
        resume_step = int(meta.get("resume_step", 1) or 1)
        resume_skill = (
            meta.get("mission", {}).get("steps", [{}])[max(resume_step - 1, 0)].get("skill_id", "")
        )
        self._approved_steps.add((str(job), resume_step, resume_skill))
        rr = self.mission_runner.run(
            mission,
            start_step=resume_step,
            context={"queue_job": str(job), "approval_resumed": True},
        )
        if rr.data.get("status") == "pending_approval":
            self._write_pending_artifacts(job, payload=payload, mission=mission, run_data=rr.data)
            log(
                {
                    "event": "queue_job_pending_approval",
                    "job": str(job),
                    "step": rr.data.get("step"),
                    "reason": rr.data.get("reason"),
                }
            )
            return False
        if not rr.ok:
            moved = self._move_job(job, self.failed)
            error_text = rr.error or "mission failed"
            self._write_failed_error_sidecar(
                moved, error=error_text, payload=payload if isinstance(payload, dict) else None
            )
            self.stats.failed += 1
            log({"event": "queue_job_failed", "job": str(moved), "error": error_text})
            self.prune_failed_artifacts()
            meta_path.unlink(missing_ok=True)
            artifact_path.unlink(missing_ok=True)
            return False

        moved = self._move_job(job, self.done)
        self.stats.processed += 1
        log({"event": "queue_job_done", "job": str(moved), "via": "approval_inbox"})
        meta_path.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)
        return True

    def process_pending_once(self) -> int:
        self.ensure_dirs()
        processed = 0
        for job in sorted(self.inbox.glob("*.json")):
            if not self._is_ready_job_file(job):
                continue
            self.process_job_file(job)
            processed += 1
        return processed

    def run(self, once: bool = False) -> None:
        self.ensure_dirs()
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
                    if daemon._is_ready_job_file(path):
                        daemon.process_job_file(path)

            observer = Observer()
            observer.schedule(_Handler(), str(self.inbox), recursive=False)
            observer.start()
            log({"event": "queue_watch_mode", "mode": "watchdog"})
            self.process_pending_once()
            try:
                while True:
                    time.sleep(self.poll_interval)
            finally:
                observer.stop()
                observer.join()
        except ImportError:
            log({"event": "queue_watch_mode", "mode": "poll"})
            while True:
                self.process_pending_once()
                time.sleep(self.poll_interval)
