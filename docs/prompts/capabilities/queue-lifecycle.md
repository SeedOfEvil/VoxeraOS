# Capability: Queue Lifecycle

Queue state is authoritative for submitted lifecycle truth.
Conversational claims must never override queue truth.

## Queue Buckets
- `inbox/`: intake of newly submitted payloads
- `pending/`: active jobs in planning/running or non-terminal progression
- `pending/approvals/`: jobs paused for approval gates
- `done/`: successful terminal outcomes
- `failed/`: failed terminal outcomes
- `canceled/`: canceled terminal outcomes

Recovery/quarantine/archive paths exist for safe remediation, startup recovery, and retention hygiene.

## Canonical Lifecycle Shape
`queued -> planning -> running -> awaiting approval -> resumed|failed -> done|failed|canceled`

Queue-owned sidecars and artifacts are the canonical source for submitted lifecycle state.
