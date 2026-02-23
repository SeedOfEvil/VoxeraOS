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
from ..paths import queue_root as default_queue_root
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
_APPROVAL_GRANTS_FILE = "grants.json"


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
        self.pending = self.queue_root / "pending"
        self.approvals = self.pending / "approvals"
        self.artifacts = self.queue_root / "artifacts"
        self.archive = self.queue_root / "_archive"
        self.pause_marker = self.queue_root / ".paused"
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
        self.pending.mkdir(parents=True, exist_ok=True)
        self.approvals.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)

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
            shutil.move(str(src), str(target))
        except FileNotFoundError:
            log(
                {
                    "event": "queue_job_already_moved",
                    "job": str(src),
                    "target_dir": str(target_dir),
                }
            )
            return None
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

    def _build_mission_for_payload(
        self, payload: dict[str, Any], *, job_ref: str
    ) -> MissionTemplate:
        normalized = self._normalize_payload(payload)
        if "mission_id" in normalized:
            return get_mission(normalized["mission_id"])
        if "steps" in normalized:
            return self._build_inline_mission(normalized, job_ref=job_ref)
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
            (".pending.json", ".approval.json", ".error.json", ".tmp.json", ".partial.json")
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
        return {
            "queue_root": str(self.queue_root),
            "exists": self.queue_root.exists(),
            "counts": {
                "inbox": self._count_files(self.inbox, "*.json"),
                "pending": len(self._pending_primary_jobs()),
                "pending_approvals": self._count_files(self.approvals, "*.approval.json"),
                "done": self._count_files(self.done, "*.json"),
                "failed": len(failed_files),
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
        }

    def _resolve_job_ref_in_dirs(self, ref: str, directories: list[Path]) -> Path | None:
        raw = ref.strip()
        if not raw:
            return None
        direct = Path(raw).expanduser()
        if direct.exists() and direct.is_file():
            return direct
        base = Path(raw).name
        stem = Path(base).stem
        candidates = {base, f"{stem}.json", f"job-{stem}.json"}
        for directory in directories:
            for cand in candidates:
                path = directory / cand
                if path.exists() and path.is_file():
                    return path
        return None

    def cancel_job(self, ref: str) -> Path:
        self.ensure_dirs()
        job = self._resolve_job_ref_in_dirs(ref, [self.inbox, self.pending, self.done, self.failed])
        if job is None:
            raise FileNotFoundError(f"job not found: {ref}")

        if job.parent == self.failed:
            return job

        moved = self._move_job(job, self.failed)
        if moved is None:
            raise FileNotFoundError(f"job not found: {ref}")
        self._write_failed_error_sidecar(moved, error="cancelled by operator", payload=None)
        (self.pending / f"{moved.stem}.pending.json").unlink(missing_ok=True)
        (self.approvals / f"{moved.stem}.approval.json").unlink(missing_ok=True)
        log({"event": "queue_job_cancel", "ref": ref, "job": str(moved)})
        return moved

    def retry_job(self, ref: str) -> Path:
        self.ensure_dirs()
        failed_job = self._resolve_job_ref_in_dirs(ref, [self.failed])
        if failed_job is None:
            raise FileNotFoundError(f"failed job not found: {ref}")
        self._failed_error_sidecar(failed_job).unlink(missing_ok=True)
        moved = self._move_job(failed_job, self.inbox)
        if moved is None:
            raise FileNotFoundError(f"failed job not found: {ref}")
        log(
            {
                "event": "queue_job_retry",
                "original_failed_job": str(failed_job),
                "new_attempt": str(moved),
            }
        )
        return moved

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
            self._write_plan_artifact(str(job_path), payload=payload, mission=mission)
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
            if moved is None:
                return False
            self._write_pending_artifacts(moved, payload=payload, mission=mission, run_data=rr.data)
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
        self._write_action_event(str(moved), "queue_job_done")
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

    def resolve_approval(self, ref: str, *, approve: bool, approve_always: bool = False) -> bool:
        self.ensure_dirs()
        job = self._find_pending_job(ref)
        meta_path = self.pending / f"{job.stem}.pending.json"
        artifact_path = self.approvals / f"{job.stem}.approval.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"approval metadata missing for {job.name}")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
        self._write_action_event(str(moved), "queue_job_done", via="approval_inbox")
        log({"event": "queue_job_done", "job": str(moved), "via": "approval_inbox"})
        meta_path.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)
        return True

    def process_pending_once(self) -> int:
        self.ensure_dirs()
        self._auto_relocate_legacy_jobs()
        self._auto_relocate_misplaced_pending_jobs()
        if self.is_paused():
            log({"event": "queue_tick_paused", "queue": str(self.inbox)})
            return 0
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
