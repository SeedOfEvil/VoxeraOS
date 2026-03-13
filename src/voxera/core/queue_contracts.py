from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .missions import MissionTemplate
from .simple_intent import sanitize_serialized_intent_route

EXECUTION_ENVELOPE_SCHEMA_VERSION = 1
STEP_RESULT_SCHEMA_VERSION = 1
EXECUTION_RESULT_SCHEMA_VERSION = 1
EVIDENCE_BUNDLE_SCHEMA_VERSION = 1
REVIEW_SUMMARY_SCHEMA_VERSION = 1
_LINEAGE_ROLES = frozenset({"root", "child"})


def _sanitize_lineage_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _sanitize_lineage_int(value: Any, *, allow_zero: bool = True) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    minimum = 0 if allow_zero else 1
    return parsed if parsed >= minimum else None


def extract_lineage_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    source = payload
    if isinstance(payload.get("lineage"), dict):
        source = payload["lineage"]

    parent_job_id = _sanitize_lineage_string(source.get("parent_job_id"))
    root_job_id = _sanitize_lineage_string(source.get("root_job_id"))
    orchestration_depth = _sanitize_lineage_int(source.get("orchestration_depth"))
    sequence_index = _sanitize_lineage_int(source.get("sequence_index"))
    raw_lineage_role = _sanitize_lineage_string(source.get("lineage_role"))
    lineage_role = (
        raw_lineage_role.lower()
        if raw_lineage_role and raw_lineage_role.lower() in _LINEAGE_ROLES
        else None
    )

    has_any_lineage = any(
        key in source
        for key in (
            "parent_job_id",
            "root_job_id",
            "orchestration_depth",
            "sequence_index",
            "lineage_role",
        )
    )
    if not has_any_lineage:
        return None

    return {
        "parent_job_id": parent_job_id,
        "root_job_id": root_job_id,
        "orchestration_depth": orchestration_depth if orchestration_depth is not None else 0,
        "sequence_index": sequence_index,
        "lineage_role": lineage_role,
    }


def extract_enqueue_child_request(payload: dict[str, Any]) -> dict[str, str | None] | None:
    if "enqueue_child" not in payload:
        return None
    raw = payload.get("enqueue_child")
    if not isinstance(raw, dict):
        raise ValueError("enqueue_child must be an object")

    allowed_keys = {"goal", "title"}
    unknown_keys = sorted(set(raw.keys()) - allowed_keys)
    if unknown_keys:
        joined = ", ".join(unknown_keys)
        raise ValueError(f"enqueue_child contains unsupported keys: {joined}")

    child_goal = _sanitize_lineage_string(raw.get("goal"))
    if child_goal is None:
        raise ValueError("enqueue_child.goal must be a non-empty string")

    child_title = _sanitize_lineage_string(raw.get("title"))
    return {"goal": child_goal, "title": child_title}


def extract_write_file_request(payload: dict[str, Any]) -> dict[str, str] | None:
    if "write_file" not in payload:
        return None
    raw = payload.get("write_file")
    if not isinstance(raw, dict):
        raise ValueError("write_file must be an object")

    allowed_keys = {"path", "content", "mode"}
    unknown_keys = sorted(set(raw.keys()) - allowed_keys)
    if unknown_keys:
        joined = ", ".join(unknown_keys)
        raise ValueError(f"write_file contains unsupported keys: {joined}")

    path = _sanitize_lineage_string(raw.get("path"))
    if path is None:
        raise ValueError("write_file.path must be a non-empty string")

    content = raw.get("content")
    if not isinstance(content, str):
        raise ValueError("write_file.content must be a string")

    mode = _sanitize_lineage_string(raw.get("mode")) or "overwrite"
    normalized_mode = mode.lower()
    if normalized_mode not in {"overwrite", "append"}:
        raise ValueError("write_file.mode must be overwrite or append")

    return {"path": path, "content": content, "mode": normalized_mode}


def compute_child_lineage(
    *,
    parent_job_id: str,
    parent_payload: dict[str, Any],
) -> dict[str, Any]:
    parent_lineage = extract_lineage_metadata(parent_payload) or {
        "parent_job_id": None,
        "root_job_id": None,
        "orchestration_depth": 0,
        "sequence_index": None,
        "lineage_role": None,
    }

    parent_root_job_id = _sanitize_lineage_string(parent_lineage.get("root_job_id"))
    parent_depth = _sanitize_lineage_int(parent_lineage.get("orchestration_depth")) or 0
    parent_sequence = _sanitize_lineage_int(parent_lineage.get("sequence_index"))

    return {
        "parent_job_id": parent_job_id,
        "root_job_id": parent_root_job_id or parent_job_id,
        "orchestration_depth": parent_depth + 1,
        "sequence_index": (parent_sequence + 1) if parent_sequence is not None else 1,
        "lineage_role": "child",
    }


def detect_request_kind(payload: dict[str, Any]) -> str:
    intent = payload.get("job_intent")
    if isinstance(intent, dict):
        intent_kind = str(intent.get("request_kind") or "").strip()
        if intent_kind:
            return intent_kind
    kind = str(payload.get("kind") or "").strip()
    if kind:
        return kind
    if payload.get("mission_id") or payload.get("mission"):
        return "mission_id"
    if payload.get("goal") or payload.get("plan_goal"):
        return "goal"
    if payload.get("steps"):
        return "inline_steps"
    return "unknown"


def build_execution_envelope(
    *,
    job_ref: str,
    payload: dict[str, Any],
    mission: MissionTemplate,
    queue_root: Path,
    artifact_root: Path,
    normalized_mode: str,
    attempt_index: int = 1,
    replan_count: int = 0,
    max_replans: int = 1,
    supersedes_attempt: int | None = None,
    execution_lane: str = "queue",
    fast_lane: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lineage = extract_lineage_metadata(payload)
    return {
        "schema_version": EXECUTION_ENVELOPE_SCHEMA_VERSION,
        "envelope_kind": "queue_execution",
        "job": {
            "id": f"{Path(job_ref).stem}.json",
            "filename": Path(job_ref).name,
            "source_ref": job_ref,
            "request_kind": detect_request_kind(payload),
            "lineage": lineage,
        },
        "execution": {
            "mode": normalized_mode,
            "lane": execution_lane,
            "fast_lane": fast_lane if isinstance(fast_lane, dict) else None,
            "attempt_index": attempt_index,
            "replan_count": replan_count,
            "max_replans": max_replans,
            "supersedes_attempt": supersedes_attempt,
            "total_steps": len(mission.steps),
            "steps": [
                {
                    "step_index": idx,
                    "total_steps": len(mission.steps),
                    "skill_id": step.skill_id,
                    "effective_args": step.args,
                }
                for idx, step in enumerate(mission.steps, start=1)
            ],
        },
        "request": {
            "mission_id": payload.get("mission_id"),
            "goal": payload.get("goal"),
            "title": payload.get("title"),
            "approval_required": payload.get("approval_required") is True,
            "job_intent": payload.get("job_intent")
            if isinstance(payload.get("job_intent"), dict)
            else None,
            "simple_intent": (
                sanitize_serialized_intent_route(payload.get("_simple_intent"))
                if isinstance(payload.get("_simple_intent"), dict)
                else None
            ),
            "write_file": extract_write_file_request(payload),
        },
        "mission": {
            "id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
            "notes": mission.notes,
        },
        "queue": {
            "queue_root": str(queue_root),
            "artifacts_root": str(artifact_root),
            "job_artifacts": str(artifact_root / Path(job_ref).stem),
        },
        "generated_at_ms": int(time.time() * 1000),
    }


def build_assistant_execution_envelope(
    *,
    job_ref: str,
    payload: dict[str, Any],
    queue_root: Path,
    artifact_root: Path,
    execution_lane: str,
    fast_lane: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thread_id = str(payload.get("thread_id") or "")
    question = str(payload.get("question") or "")
    request_kind = detect_request_kind(payload)
    lineage = extract_lineage_metadata(payload)
    return {
        "schema_version": EXECUTION_ENVELOPE_SCHEMA_VERSION,
        "envelope_kind": "queue_execution",
        "job": {
            "id": f"{Path(job_ref).stem}.json",
            "filename": Path(job_ref).name,
            "source_ref": job_ref,
            "request_kind": request_kind,
            "lineage": lineage,
        },
        "execution": {
            "mode": "assistant_advisory",
            "lane": execution_lane,
            "fast_lane": fast_lane if isinstance(fast_lane, dict) else None,
            "attempt_index": 1,
            "replan_count": 0,
            "max_replans": 0,
            "supersedes_attempt": None,
            "total_steps": 1,
            "steps": [
                {
                    "step_index": 1,
                    "total_steps": 1,
                    "skill_id": "assistant.advisory",
                    "effective_args": {
                        "question": question,
                        "thread_id": thread_id,
                    },
                }
            ],
        },
        "request": {
            "mission_id": None,
            "goal": None,
            "title": payload.get("title"),
            "approval_required": payload.get("approval_required") is True,
            "job_intent": payload.get("job_intent")
            if isinstance(payload.get("job_intent"), dict)
            else None,
            "assistant": {
                "kind": str(payload.get("kind") or request_kind),
                "thread_id": thread_id,
                "question": question,
                "advisory": payload.get("advisory") is True,
                "read_only": payload.get("read_only") is True,
                "action_hints": payload.get("action_hints")
                if isinstance(payload.get("action_hints"), list)
                else [],
            },
        },
        "mission": None,
        "queue": {
            "queue_root": str(queue_root),
            "artifacts_root": str(artifact_root),
            "job_artifacts": str(artifact_root / Path(job_ref).stem),
        },
        "generated_at_ms": int(time.time() * 1000),
    }


def build_structured_step_results(
    rr_data: dict[str, Any],
    *,
    total_steps: int,
    existing_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_results_any = rr_data.get("results")
    raw_results: list[Any] = raw_results_any if isinstance(raw_results_any, list) else []
    raw_outcomes_any = rr_data.get("step_outcomes")
    raw_outcomes: list[Any] = raw_outcomes_any if isinstance(raw_outcomes_any, list) else []
    outcome_by_step: dict[int, dict[str, Any]] = {}
    for item in raw_outcomes:
        if not isinstance(item, dict):
            continue
        try:
            step_no = int(item.get("step") or 0)
        except (TypeError, ValueError):
            continue
        if step_no <= 0:
            continue
        outcome_by_step[step_no] = item

    structured: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        step_index = int(item.get("step") or 0)
        if step_index <= 0:
            continue
        outcome = outcome_by_step.get(step_index, {})
        status = str(outcome.get("outcome") or ("succeeded" if item.get("ok") else "failed"))
        started_at_ms = int(item.get("started_at_ms") or 0) or None
        finished_at_ms = int(item.get("finished_at_ms") or 0) or None
        duration_ms = int(item.get("duration_ms") or 0) or None
        structured.append(
            {
                "schema_version": STEP_RESULT_SCHEMA_VERSION,
                "step_index": step_index,
                "step_total": int(total_steps),
                "skill_id": str(item.get("skill") or ""),
                "effective_args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "status": status,
                "started_at_ms": started_at_ms,
                "finished_at_ms": finished_at_ms,
                "duration_ms": duration_ms,
                "summary": str(
                    item.get("summary") or item.get("output") or item.get("error") or ""
                ),
                "output_artifacts": item.get("output_artifacts")
                if isinstance(item.get("output_artifacts"), list)
                else [],
                "machine_payload": item.get("machine_payload")
                if isinstance(item.get("machine_payload"), dict)
                else {},
                "operator_note": item.get("operator_note"),
                "next_action_hint": item.get("next_action_hint"),
                "retryable": item.get("retryable")
                if isinstance(item.get("retryable"), bool)
                else None,
                "blocked": (
                    item.get("blocked")
                    if isinstance(item.get("blocked"), bool)
                    else status == "blocked"
                ),
                "approval_status": item.get("approval_status") or outcome.get("approval_status"),
                "error": str(item.get("error") or "") or None,
                "error_class": item.get("error_class"),
            }
        )

    if structured:
        return structured
    if existing_results:
        return existing_results
    return []


def build_execution_result(
    *,
    job_ref: str,
    rr_data: dict[str, Any],
    step_results: list[dict[str, Any]],
    terminal_outcome: str,
    ok: bool,
    error: str | None = None,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    lineage = extract_lineage_metadata(rr_data)
    artifact_families, artifact_refs = _build_artifact_contract(artifacts_dir)
    execution_capabilities = _extract_execution_capabilities(
        rr_data=rr_data, step_results=step_results
    )
    expected_artifacts = _normalize_expected_artifacts(
        execution_capabilities.get("expected_artifacts") if execution_capabilities else []
    )
    expected_artifact_observation = _compare_expected_artifacts(
        expected_artifacts=expected_artifacts,
        artifact_families=artifact_families,
        artifact_refs=artifact_refs,
    )
    review_summary = _build_review_summary(
        job_ref=job_ref,
        rr_data=rr_data,
        step_results=step_results,
        terminal_outcome=terminal_outcome,
        ok=ok,
        error=error,
        execution_capabilities=execution_capabilities,
        expected_artifact_observation=expected_artifact_observation,
    )
    evidence_bundle = _build_evidence_bundle(
        job_ref=job_ref,
        rr_data=rr_data,
        terminal_outcome=terminal_outcome,
        artifact_families=artifact_families,
        artifact_refs=artifact_refs,
        review_summary=review_summary,
        expected_artifact_observation=expected_artifact_observation,
    )
    return {
        "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
        "job": Path(job_ref).name,
        "lineage": lineage,
        "ok": ok,
        "terminal_outcome": terminal_outcome,
        "execution_lane": str(rr_data.get("execution_lane") or "queue"),
        "fast_lane": rr_data.get("fast_lane")
        if isinstance(rr_data.get("fast_lane"), dict)
        else None,
        "lifecycle_state": rr_data.get("lifecycle_state"),
        "current_step_index": rr_data.get("current_step_index"),
        "last_completed_step": rr_data.get("last_completed_step"),
        "last_attempted_step": rr_data.get("last_attempted_step"),
        "total_steps": rr_data.get("total_steps"),
        "attempt_index": rr_data.get("attempt_index"),
        "replan_count": rr_data.get("replan_count"),
        "max_replans": rr_data.get("max_replans"),
        "evaluation_class": rr_data.get("evaluation_class"),
        "evaluation_reason": rr_data.get("evaluation_reason"),
        "stop_reason": rr_data.get("stop_reason"),
        "intent_route": (
            sanitize_serialized_intent_route(rr_data.get("intent_route"))
            if isinstance(rr_data.get("intent_route"), dict)
            else None
        ),
        "child_refs": [item for item in rr_data.get("child_refs", []) if isinstance(item, dict)]
        if isinstance(rr_data.get("child_refs"), list)
        else [],
        "approval_status": (
            rr_data.get("approval_status")
            if isinstance(rr_data.get("approval_status"), str)
            and str(rr_data.get("approval_status") or "").strip()
            else "pending"
            if rr_data.get("status") == "pending_approval"
            else "approved"
            if terminal_outcome == "succeeded"
            else None
        ),
        "artifact_families": artifact_families,
        "artifact_refs": artifact_refs,
        "evidence_bundle": evidence_bundle,
        "review_summary": review_summary,
        "step_results": step_results,
        "error": error,
        "updated_at_ms": int(time.time() * 1000),
    }


def _extract_execution_capabilities(
    *, rr_data: dict[str, Any], step_results: list[dict[str, Any]]
) -> dict[str, Any] | None:
    direct = rr_data.get("execution_capabilities")
    if isinstance(direct, dict):
        return direct
    for step in reversed(step_results):
        machine_payload = step.get("machine_payload")
        if not isinstance(machine_payload, dict):
            continue
        nested = machine_payload.get("execution_capabilities")
        if isinstance(nested, dict):
            return nested
    return None


def _normalize_expected_artifacts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = sorted({str(item).strip() for item in value if str(item).strip()})
    return normalized


def _compare_expected_artifacts(
    *,
    expected_artifacts: list[str],
    artifact_families: list[str],
    artifact_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    if not expected_artifacts:
        return {
            "status": "none_declared",
            "expected": [],
            "observed": [],
            "missing": [],
        }

    observed_families = {str(item).strip() for item in artifact_families if str(item).strip()}
    observed_paths = {
        str(item.get("artifact_path") or "").strip()
        for item in artifact_refs
        if isinstance(item, dict) and str(item.get("artifact_path") or "").strip()
    }
    observed_path_stems = {Path(path).stem for path in observed_paths}
    observed: list[str] = []
    missing: list[str] = []
    for expected in expected_artifacts:
        expected_token = expected.strip()
        expected_stem = Path(expected_token).stem
        if (
            expected_token in observed_families
            or expected_token in observed_paths
            or expected_stem in observed_families
            or expected_stem in observed_path_stems
        ):
            observed.append(expected_token)
        else:
            missing.append(expected_token)

    if not missing:
        status = "observed"
    elif observed:
        status = "partial"
    else:
        status = "missing"
    return {
        "status": status,
        "expected": expected_artifacts,
        "observed": observed,
        "missing": missing,
    }


def _build_artifact_contract(artifacts_dir: Path | None) -> tuple[list[str], list[dict[str, Any]]]:
    if artifacts_dir is None:
        return (["step_results", "execution_result"], [])

    file_to_family = {
        "plan.json": "plan",
        "actions.jsonl": "actions",
        "stdout.txt": "stdout",
        "stderr.txt": "stderr",
        "execution_envelope.json": "execution_envelope",
        "execution_result.json": "execution_result",
        "step_results.json": "step_results",
        "job_intent.json": "job_intent",
        "assistant_response.json": "assistant_advisory",
        "review_summary.json": "review_summary",
        "evidence_bundle.json": "evidence_bundle",
    }
    refs: list[dict[str, Any]] = []
    families: set[str] = {"step_results", "execution_result"}
    for filename, family in file_to_family.items():
        path = artifacts_dir / filename
        if not path.exists():
            continue
        families.add(family)
        refs.append(
            {
                "artifact_family": family,
                "artifact_path": filename,
                "exists": True,
            }
        )

    refs.sort(key=lambda item: str(item.get("artifact_path") or ""))
    return sorted(families), refs


def _build_review_summary(
    *,
    job_ref: str,
    rr_data: dict[str, Any],
    step_results: list[dict[str, Any]],
    terminal_outcome: str,
    ok: bool,
    error: str | None,
    execution_capabilities: dict[str, Any] | None,
    expected_artifact_observation: dict[str, Any],
) -> dict[str, Any]:
    latest_step = step_results[-1] if step_results else {}
    latest_summary = str(
        latest_step.get("summary") or latest_step.get("operator_note") or error or ""
    )
    return {
        "schema_version": REVIEW_SUMMARY_SCHEMA_VERSION,
        "job_id": Path(job_ref).name,
        "terminal_outcome": terminal_outcome,
        "ok": ok,
        "execution_lane": str(rr_data.get("execution_lane") or "queue"),
        "lifecycle_state": rr_data.get("lifecycle_state"),
        "approval_status": rr_data.get("approval_status"),
        "attempt_index": rr_data.get("attempt_index"),
        "replan_count": rr_data.get("replan_count"),
        "total_steps": rr_data.get("total_steps"),
        "last_attempted_step": rr_data.get("last_attempted_step"),
        "last_completed_step": rr_data.get("last_completed_step"),
        "latest_step_status": latest_step.get("status"),
        "latest_summary": latest_summary,
        "error": error,
        "execution_capabilities": execution_capabilities,
        "expected_artifacts": expected_artifact_observation.get("expected", []),
        "expected_artifact_status": expected_artifact_observation.get("status", "none_declared"),
        "observed_expected_artifacts": expected_artifact_observation.get("observed", []),
        "missing_expected_artifacts": expected_artifact_observation.get("missing", []),
    }


def _build_evidence_bundle(
    *,
    job_ref: str,
    rr_data: dict[str, Any],
    terminal_outcome: str,
    artifact_families: list[str],
    artifact_refs: list[dict[str, Any]],
    review_summary: dict[str, Any],
    expected_artifact_observation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "bundle_kind": "queue_job_execution_evidence",
        "job_id": Path(job_ref).name,
        "trace": {
            "job_id": Path(job_ref).name,
            "attempt_index": rr_data.get("attempt_index"),
            "replan_count": rr_data.get("replan_count"),
            "last_attempted_step": rr_data.get("last_attempted_step"),
            "last_completed_step": rr_data.get("last_completed_step"),
            "total_steps": rr_data.get("total_steps"),
            "execution_lane": str(rr_data.get("execution_lane") or "queue"),
            "terminal_outcome": terminal_outcome,
        },
        "artifact_families": artifact_families,
        "artifact_refs": artifact_refs,
        "review_summary": review_summary,
        "expected_artifacts": expected_artifact_observation,
    }
