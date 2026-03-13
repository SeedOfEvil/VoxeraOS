# Capability: Queue Lifecycle

Queue state is authoritative for submitted lifecycle truth.
Conversational claims must never override queue truth.

## Canonical Unit
- A submitted queue job (`<job>.json`) is the canonical execution contract after submit.
- Job sidecars (`<job>.state.json`) and queue-owned artifacts track lifecycle and runtime progress.

## Queue Buckets
- `inbox/`: intake of newly submitted payloads
- `pending/`: active jobs in planning/running or non-terminal progression
- `pending/approvals/`: jobs paused for approval gates (approval artifacts)
- `done/`: successful terminal outcomes
- `failed/`: failed/blocked terminal outcomes
- `canceled/`: canceled terminal outcomes
- recovery/quarantine/archive: remediation and retention context

## Canonical Lifecycle Shape
`queued -> planning -> running -> awaiting_approval -> resumed|failed -> done|failed|canceled`

Assistant/recovery variants may appear, but queue state sidecars and terminal bucket placement remain canonical submitted lifecycle truth.

## Truth Boundaries
- Preview truth ends at submit acknowledgment.
- Queue truth begins once accepted in `inbox/`.
- Artifact/evidence truth determines what actually happened after runtime execution.

## Reviewer/Verifier lifecycle output discipline
- Keep active lifecycle (`submitted|queued|planning|running|awaiting_approval|resumed`) distinct from terminal outcomes.
- Never report succeeded/done unless canonical queue placement and artifact/evidence truth support it.
- Keep canceled distinct from failed.
