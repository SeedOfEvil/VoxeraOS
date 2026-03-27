# Missions

Voxera OS treats every operator request as a Mission: a structured unit of intent that flows through planning, policy evaluation, approval gating (when required), execution, and audit.

## Mission execution model

```
Goal/Prompt → Normalize → Plan → Policy → Approval (if ASK) → Execute → Audit → Artifacts
```

Every mission step:
- Declares the skill and arguments it needs.
- Passes through the policy engine (allow / ask / deny).
- Produces a canonical `skill_result` with `summary`, `machine_payload`, `operator_note`, `retryable`, `blocked`, and `approval_status`.
- Is recorded in `actions.jsonl`, `step_results.json`, and `execution_result.json`.

Missions that require approval pause at `pending/approvals/` and resume only when the operator explicitly approves or denies via CLI or panel.

## Built-in mission templates

Pre-defined in-code mission templates (in `src/voxera/core/missions.py`):
- `work_mode`
- `focus_mode`
- `daily_checkin`
- `incident_mode`
- `wrap_up`
- `system_check`
- `notes_archive_flow`
- `system_inspect`
- `system_diagnostics`

Additional file-based missions are also loaded from repository `missions/` and user `~/.config/voxera/missions` when valid.

## Cloud-assisted mission planning

For goal-string requests (not pre-defined templates), the queue daemon uses the cloud-assisted mission planner:

```bash
voxera inbox add "Run a quick system check"
voxera daemon --once
```

Planning uses a deterministic fallback sequence (`primary` → `fast` → `reasoning` → `fallback`). The planner returns a structured plan; policy, approvals, and skill execution happen locally. Cloud planning only shapes the plan; it does not bypass any control plane gate.

## Intent routing guardrail

Goal-kind requests pass through the deterministic simple-intent classifier before cloud planning. Common patterns like `open_terminal`, `open_url`, `open_app`, `write_file`, `read_file`, and `run_command` are routed to allowed skill families and fail closed if the planner's first step doesn't match. See `docs/ARCHITECTURE.md` for the full intent routing contract.

## Artifacts produced per mission

For each queue job, the following artifacts are written under `artifacts/<job-id>/`:
- `job_intent.json` — producer-side intent metadata (request kind, goal, candidate skills).
- `execution_envelope.json` — normalized job snapshot at start of execution (plan, intent, lineage, lane).
- `step_results.json` — per-step canonical skill results.
- `execution_result.json` — terminal outcome (succeeded/failed/blocked/canceled), evaluation class, intent route.
- `plan.json` — cloud-planned mission (if planner was used).
- `child_job_refs.json` — if the mission requested a child enqueue (PR #149).
- `actions.jsonl` — append-only audit event stream.

## CLI workflows

```bash
# Submit a goal via inbox
voxera inbox add "Open a terminal"

# Run the daemon to process it
voxera daemon --once

# Check job status
voxera queue status

# List pending approvals
voxera queue approvals list

# Approve or deny
voxera queue approvals approve <job_ref>
voxera queue approvals deny <job_ref>

# Dry-run a mission template
voxera missions plan "prep a focused work session" --dry-run

# Run a named mission
voxera missions run work_mode
```

## Panel workflows

The panel (`voxera panel --host 127.0.0.1 --port 8844`) provides (note: `make panel` uses `127.0.0.1:8787` for local-dev convenience):
- Job list with lifecycle buckets (inbox / pending / done / failed / canceled).
- Per-job detail with live progress polling (`/jobs/{id}/progress`) — added in PR #146.
- Approval/deny/retry/cancel actions.
- Mission creation from templates or custom goals.
- Child job visibility under parent job detail (PR #149).
- Lineage metadata display when present (PR #148).

## Current status (v0.1.8 branch truth-sync)

- Mission execution, approval flows, and audit trail: implemented and operator-visible.
- Intent routing guardrail (PRs #144–#145): deterministic classifier runs before planning; fail-closed on skill family mismatch.
- Queue lineage metadata (PR #148): parent/child relationships tracked additively; observational only.
- Controlled child enqueue (PR #149): missions can request one child job; child enters normal queue lifecycle with full policy/approval enforcement.
- Live progress (PR #146): panel job detail pages poll for real-time lifecycle/step updates from canonical artifacts.
- Mission catalog: nine built-in in-code templates ship today, plus file-based mission loading; broader catalog growth remains a future milestone.


## Structured bounded file-organize queue jobs

Queue producers can submit a deterministic bounded file workflow using `file_organize`:

```json
{
  "file_organize": {
    "source_path": "~/VoxeraOS/notes/inbox/today.md",
    "destination_dir": "~/VoxeraOS/notes/archive/2026-03",
    "mode": "copy",
    "overwrite": false,
    "delete_original": false
  }
}
```

Execution composes confined file skills as one mission:
1. `files.exists` (preflight source check)
2. `files.stat` (metadata evidence)
3. `files.mkdir` (ensure destination dir)
4. `files.copy_file` or `files.move_file`
5. optional `files.delete_file` only when `delete_original=true`

All paths remain constrained to `~/VoxeraOS/notes/**`, and control-plane paths under `~/VoxeraOS/notes/queue/**` are rejected fail-closed by path-boundary enforcement.
