from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..health import record_mission_success
from .execution_capabilities import normalize_manifest_capabilities
from .missions import MissionStep, MissionTemplate


def _queue_daemon_module() -> Any:
    from . import queue_daemon

    return queue_daemon


def _log(event: dict[str, Any]) -> None:
    _queue_daemon_module().log(event)


def _subprocess_run(*args: Any, **kwargs: Any) -> Any:
    return _queue_daemon_module().subprocess.run(*args, **kwargs)


_AUTO_APPROVE_ALLOWLIST = {"system.settings"}
_APPROVAL_GRANTS_FILE = "grants.json"


class QueueApprovalMixin:
    current_job_ref: Any

    def _decision_capability(self: Any, decision) -> str:
        first = (decision.reason or "").split(";", 1)[0].strip()
        return first.split(" ->", 1)[0].strip() if "->" in first else "unknown"

    def _queue_approval_prompt(self: Any, manifest, decision, *, audit_context=None, args=None):
        capability = self._decision_capability(decision)
        step = (audit_context or {}).get("step")
        reason = decision.reason
        redacted_args = self._redact_args(args or {})
        target = self._approval_target(manifest.id, args or {})
        scope = {
            "fs_scope": manifest.fs_scope,
            "needs_network": bool(manifest.needs_network),
        }
        execution_capabilities = normalize_manifest_capabilities(manifest).as_dict()

        approval_key = (self.current_job_ref or "", int(step or 0), manifest.id)
        if approval_key in self._approved_steps:
            self._approved_steps.discard(approval_key)
            return True

        if self._has_approval_grant(manifest.id, capability, scope):
            _log(
                {
                    "event": "queue_grant_auto_approved",
                    "job": self.current_job_ref,
                    "step": step,
                    "skill": manifest.id,
                    "capability": capability,
                    "scope": scope,
                    "execution_capabilities": execution_capabilities,
                }
            )
            return True

        if self.auto_approve_ask and self.dev_mode and capability in _AUTO_APPROVE_ALLOWLIST:
            _log(
                {
                    "event": "queue_auto_approved",
                    "job": self.current_job_ref,
                    "step": step,
                    "skill": manifest.id,
                    "reason": reason,
                    "capability": capability,
                    "target": target,
                    "scope": scope,
                    "execution_capabilities": execution_capabilities,
                }
            )
            return True

        _log(
            {
                "event": "queue_approval_required",
                "job": self.current_job_ref,
                "step": step,
                "skill": manifest.id,
                "reason": reason,
                "capability": capability,
                "target": target,
                "scope": scope,
                "execution_capabilities": execution_capabilities,
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
            "execution_capabilities": execution_capabilities,
        }

    def _approval_target(self: Any, skill_id: str, args: dict[str, Any]) -> dict[str, str]:
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

    def _grants_path(self: Any) -> Path:
        return self.approvals / _APPROVAL_GRANTS_FILE

    def _read_grants(self: Any) -> list[dict[str, Any]]:
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

    def _write_grants(self: Any, grants: list[dict[str, Any]]) -> None:
        self._grants_path().write_text(json.dumps(grants, indent=2), encoding="utf-8")

    def grant_approval_scope(
        self: Any, *, skill: str, capability: str, scope: dict[str, Any]
    ) -> None:
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

    def _has_approval_grant(self: Any, skill: str, capability: str, scope: dict[str, Any]) -> bool:
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

    def _write_pending_artifacts(
        self: Any,
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
            "execution_capabilities": run_data.get("execution_capabilities", {}),
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

    def _is_hard_approval_required(self: Any, payload: dict[str, Any]) -> bool:
        return bool(payload.get("approval_required") is True)

    def _ensure_hard_approval_gate(self: Any, job_path: Path, *, payload: dict[str, Any]) -> bool:
        if not self._is_hard_approval_required(payload):
            self._increment_health_counter("approval_gate_skipped_no_flag")
            _log({"event": "queue_approval_gate_skipped_no_flag", "job": str(job_path)})
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
            _log(
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
        _log(
            {
                "event": "queue_approval_gate_created",
                "job": str(job_in_pending),
                "reason": "approval_required=true hard gate",
            }
        )
        _log(
            {
                "event": "queue_job_pending_approval",
                "job": str(job_in_pending),
                "step": 0,
                "reason": "approval_required=true hard gate",
            }
        )
        return True

    def _notify_pending_approval(self: Any, approval: dict[str, Any]) -> None:
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
            result = _subprocess_run(
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
                _log({"event": "queue_notify_sent", "job": job, "skill": skill, "reason": reason})
                return
            stderr = (
                result.stderr or ""
            ).strip() or f"notify-send exited with code {result.returncode}"
            _log(
                {
                    "event": "queue_notify_failed",
                    "job": job,
                    "skill": skill,
                    "reason": reason,
                    "error": stderr,
                }
            )
        except Exception as exc:
            _log(
                {
                    "event": "queue_notify_failed",
                    "job": job,
                    "skill": skill,
                    "reason": reason,
                    "error": repr(exc),
                }
            )

    def _approval_ref_variants(self: Any, path: Path) -> set[str]:
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

    def _canonical_job_name(self: Any, artifact: Path, data: dict[str, Any]) -> str:
        job = Path(str(data.get("job") or "")).name
        if job and not job.endswith(".approval.json"):
            return job
        stem = artifact.stem.removesuffix(".approval")
        return f"{stem}.json"

    def _iter_approval_artifacts(self: Any) -> list[Path]:
        if not self.approvals.exists():
            return []
        return sorted(
            self.approvals.glob("*.approval.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )

    def _approval_scope_from_artifact(self: Any, data: dict[str, Any]) -> dict[str, Any]:
        nested_raw = data.get("scope")
        nested: dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
        fs_scope = data.get("fs_scope", nested.get("fs_scope", "workspace_only"))
        needs_network = data.get("needs_network", nested.get("needs_network", False))
        return {"fs_scope": str(fs_scope), "needs_network": bool(needs_network)}

    def _read_approval_artifact(self: Any, artifact: Path) -> dict[str, Any]:
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
            _log(
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

    def pending_approvals_snapshot(self: Any, *, limit: int = 10) -> list[dict[str, Any]]:
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

    def _approval_ref_candidates(self: Any, ref: str) -> list[str]:
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

    def canonicalize_approval_ref(self: Any, ref: str) -> str:
        job, _meta, _artifact = self._resolve_pending_approval_paths(ref)
        return job.name

    def _resolve_pending_approval_paths(self: Any, ref: str) -> tuple[Path, Path, Path]:
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

        for artifact in self._iter_approval_artifacts():
            if Path(raw_ref).name not in self._approval_ref_variants(artifact):
                continue
            stem = artifact.stem.removesuffix(".approval")
            job = self.pending / f"{stem}.json"
            meta = self.pending / f"{stem}.pending.json"
            if job.exists() and job.is_file():
                return job, meta, self.approvals / f"{stem}.approval.json"

        raise FileNotFoundError(f"pending job not found: {ref}")

    def approvals_list(self: Any) -> list[dict[str, Any]]:
        self.ensure_dirs()
        out: list[dict[str, Any]] = []
        for artifact in self._iter_approval_artifacts():
            out.append(self._read_approval_artifact(artifact))
        return out

    def resolve_approval(
        self: Any, ref: str, *, approve: bool, approve_always: bool = False
    ) -> bool:
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
            denied_step = int(meta.get("resume_step", 1) or 1)
            mission_steps = (
                (meta.get("mission") or {}).get("steps", []) if isinstance(meta, dict) else []
            )
            denied_rr_data = {
                "lifecycle_state": "failed",
                "terminal_outcome": "failed",
                "current_step_index": denied_step,
                "last_attempted_step": denied_step,
                "last_completed_step": max(denied_step - 1, 0),
                "total_steps": len(mission_steps) if isinstance(mission_steps, list) else 0,
                "approval_status": "denied",
                "error": "Denied in approval inbox",
            }
            self._write_failed_error_sidecar(
                moved,
                error="Denied in approval inbox",
                payload=meta.get("payload") if isinstance(meta, dict) else None,
            )
            self._write_execution_result_artifacts(
                str(moved),
                rr_data=denied_rr_data,
                ok=False,
                terminal_outcome="failed",
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
                lifecycle_state="failed",
                payload=meta.get("payload") if isinstance(meta, dict) else None,
                mission=denied_mission,
                rr_data=denied_rr_data,
                terminal_outcome="failed",
                failure_summary="Denied in approval inbox",
                blocked_reason="approval denied by operator",
                approval_status="denied",
            )
            self.mission_runner._append_mission_log(denied_mission, [], status="denied")
            _log(
                {
                    "event": "mission_denied",
                    "mission": meta.get("mission", {}).get("id"),
                    "reason": "approval denied from inbox",
                }
            )
            _log(
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
                _log(
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
                _log({"event": "queue_job_failed", "job": str(moved), "error": error_text})
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
            _log({"event": "queue_job_done", "job": str(moved), "via": "approval_inbox"})
            record_mission_success(self.queue_root)
            meta_path.unlink(missing_ok=True)
            artifact_path.unlink(missing_ok=True)
            return True
        finally:
            self.current_job_ref = None
