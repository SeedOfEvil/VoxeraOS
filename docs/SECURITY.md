# Security & Safety

## Threat model

| Threat | Risk | Mitigated? |
|---|---|---|
| Accidental destructive actions (rm, installs, firewall changes) | High | ✅ Policy gates + approval workflow |
| Prompt injection via user-controlled content | High | ✅ Goal strings sanitized + 2,000-char cap before planning (PR #83) |
| Secret leakage (API keys, tokens) | High | ✅ Keyring + 0600 fallback; redacted in config show/snapshot |
| Over-permissioned skills | High | ✅ Capability declarations + policy engine |
| Panel auth brute force | Medium | Partial — password required for mutations; no rate limiting yet |
| Mid-job daemon crash leaving ambiguous state | Medium | ✅ Graceful SIGTERM handler (PR #80) + deterministic startup recovery (PR #81) |
| Artifact data accumulation | Low | ✅ `voxera artifacts prune` (v0.1.5) + `voxera queue prune` (v0.1.6) available |
| Dependency supply chain | Low | No signing; standard pip install |

---

## Current controls

### Capability-based permissions
Every skill declares the capabilities it requires (`network`, `install`, `files`, `apps`, `settings`).
The policy engine maps each capability to an allow / ask / deny decision before any execution.
Skills cannot acquire capabilities they did not declare.

### Approval workflow
Steps that hit an `ask` policy decision pause the job and write an approval artifact to
`pending/approvals/*.approval.json`. No execution happens until the operator explicitly approves.
Denials move the job to `failed/` with a record of what was denied and why.

### Audit trail
Every action is logged to JSONL (`~/.voxera/data/audit/YYYY-MM-DD.jsonl`) with:
skill ID, args (sanitized), result, policy decision, timestamp, job ID.
Audit log entries are append-only; nothing is deleted by the runtime.

### Planner output validation
The mission planner only accepts:
- Valid JSON (with malformed-JSON recovery fallback).
- Known skill IDs from the live capabilities snapshot.
- `system.open_app` targets within the configured `allowed_apps` list.

Unknown skill IDs or disallowed app targets are rejected before any execution, with closest-match suggestions.

### Secret handling
- Secrets (API keys) stored in system keyring when available.
- Fallback: `~/.config/voxera/env` (mode 0600, not checked into git).
- `voxera config show` and `voxera config snapshot` redact all secret values as `***`.
- `.env` is gitignored; secrets never land in git history.

### Sandbox execution
`sandbox.exec` runs in rootless Podman:
- `--network=none` by default; network requires explicit policy approval.
- `--read-only` root filesystem.
- Only `~/.voxera/workspace/<job_id>/` mounted writable to `/work`.
- `:Z` SELinux labeling on volume mounts.
- Artifacts stored outside container in `~/.voxera/artifacts/<job_id>/`.

### Panel auth
Web panel mutations (job lifecycle, mission create) require:
- HTTP Basic auth (`VOXERA_PANEL_OPERATOR_PASSWORD`).
- CSRF token on all POST mutation routes (enforced by `_require_mutation_guard`).

Read-only panel endpoints (queue status, job list, job detail) are accessible without auth,
so operators can inspect state safely even from untrusted environments.

**Bundle export endpoints (`GET /jobs/{job_id}/bundle`, `GET /bundle/system`) are GET handlers
protected by Basic auth only — they do not go through `_require_mutation_guard` and receive
no CSRF validation.** Each request to these endpoints generates a new archive on disk.
Operators should be aware these are browser-reachable from any tab that holds a valid session
cookie, without CSRF protection.

### Graceful daemon shutdown + startup recovery (FIXED — PR #80, #81)
The queue daemon now handles `SIGTERM`/`SIGINT` explicitly:
- Stops intake of new inbox jobs immediately on signal.
- Marks any in-flight job as `failed/` with `reason=shutdown` and writes a structured sidecar.
- Releases the daemon lock and exits cleanly within systemd's `TimeoutStopSec`.

On next daemon start, a deterministic recovery pass runs before any intake:
- Pending jobs with in-flight state markers are moved to `failed/` with `reason=recovered_after_restart`.
- Orphan approvals and state files are quarantined under `recovery/startup-<ts>/` (never deleted).
- Recovery is audited via `daemon_startup_recovery` event with counters.

### Brain fallback classification (FIXED — PR #73)
Brain fallback exceptions are classified into a stable enum before being surfaced:
`TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`.
Reason and tier transition are logged to `health.json` and surfaced in `voxera queue health`
and `voxera doctor --quick`.

---

## Known gaps (being tracked in ROADMAP.md)

### Planner goal-string hardening (FIXED — PR #83)
- Goal inputs are rejected when longer than 2,000 characters before any planner brain call.
- Goal text embedded in planner prompts is sanitized by stripping ASCII control chars (`0x00-0x1F`, `0x7F`).
- Prompt embedding normalizes whitespace (collapse runs + trim ends) so planner sees stable user input.

### Panel auth has no rate limiting (tracked: ROADMAP Day 2–3)
Repeated failed Basic auth attempts are logged but not rate-limited.
On a shared or remote host, the password endpoint is brute-forceable.

**Planned fix:** failed-attempt counter with 60-second lockout after 5 failures.
Lockout events logged as structured audit entries.

---

## Hardening backlog (ordered by priority)

1. **Goal string sanitization + length cap** — prompt injection defense layer.
2. **Panel auth rate limiting** — prevent brute force on operator password.
3. **LLM rate limiter** — prevent runaway planner calls from burning API quota.
4. **Eager skill manifest validation** — catch broken manifests at startup, not mid-job.
5. **Podman seccomp / AppArmor profiles** — tighten sandbox beyond `--read-only`.
6. **Signed skills + integrity verification** — prevent tampered skill entrypoints.
7. **Redaction pipeline for audit logs and telemetry** — strip PII and secrets from logs.
8. **Safe-mode boot** — limited skill set, no network, confirmation-only execution.

Previously tracked items now resolved:
- ~~Graceful SIGTERM handler~~ — FIXED in PR #80–#81 (graceful shutdown + startup recovery).
- ~~Artifact directory auto-pruning~~ — FIXED in v0.1.5 (`voxera artifacts prune`) + v0.1.6 (`voxera queue prune`).
- ~~Brain fallback errors unstructured~~ — FIXED in PR #73 (`BrainFallbackReason` enum).

---

## For operators

- Run `voxera doctor` before starting the daemon to verify endpoint health and auth.
- Use `voxera queue health` for a quick lock/auth/counter/fallback snapshot during incidents.
- Panel mutations require `VOXERA_PANEL_OPERATOR_PASSWORD` — if not set, the panel shows a setup-required banner with no secrets displayed.
- Audit JSONL logs are at `~/.voxera/data/audit/`. Never delete these during incident triage.
- For incident response, use `voxera ops bundle system` and `voxera ops bundle job <job>` to capture a point-in-time snapshot.
- Use `voxera queue reconcile` to detect orphan sidecars or approval mismatches after unclean shutdowns.
- See `docs/ops.md` for the full incident runbook.
