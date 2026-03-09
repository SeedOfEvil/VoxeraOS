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

Pre-defined multi-step missions available in `missions/`:
- `work_mode` — prepares a focused work session (3-step example: status check + settings + app launch).
- `focus_mode` — similar focused session template.
- `daily_checkin` — system status and health summary.
- `incident_mode` — escalated context for incident response.
- `wrap_up` — end-of-session hygiene.
- `system_check` — quick health and capability snapshot.

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

The panel (`voxera panel --host 127.0.0.1 --port 8787`) provides:
- Job list with lifecycle buckets (inbox / pending / done / failed / canceled).
- Per-job detail with live progress polling (`/jobs/{id}/progress`) — added in PR #146.
- Approval/deny/retry/cancel actions.
- Mission creation from templates or custom goals.
- Child job visibility under parent job detail (PR #149).
- Lineage metadata display when present (PR #148).

## Status as of v0.1.6 + PRs #145–#149

- Mission execution, approval flows, and audit trail: implemented and operator-visible.
- Intent routing guardrail (PRs #144–#145): deterministic classifier runs before planning; fail-closed on skill family mismatch.
- Queue lineage metadata (PR #148): parent/child relationships tracked additively; observational only.
- Controlled child enqueue (PR #149): missions can request one child job; child enters normal queue lifecycle with full policy/approval enforcement.
- Live progress (PR #146): panel job detail pages poll for real-time lifecycle/step updates from canonical artifacts.
- Mission catalog: six built-in templates ship with the repo; catalog expansion is a v0.2 milestone.
