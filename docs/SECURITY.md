# Security & Safety

## Threat model

| Threat | Risk | Mitigated? |
|---|---|---|
| Accidental destructive actions (rm, installs, firewall changes) | High | ✅ Policy gates + approval workflow |
| Prompt injection via user-controlled content | High | Partial — output validated; goal strings not yet sanitized |
| Secret leakage (API keys, tokens) | High | ✅ Keyring + 0600 fallback; redacted in config show/snapshot |
| Over-permissioned skills | High | ✅ Capability declarations + policy engine |
| Panel auth brute force | Medium | Partial — password required for mutations; no rate limiting yet |
| Mid-job daemon crash leaving ambiguous state | Medium | No — no SIGTERM handler; crash leaves job in pending/ with no sidecar guaranteed |
| Artifact data accumulation | Low | Partial — queue pruned; artifact dirs not auto-cleaned |
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

---

## Known gaps (being tracked in ROADMAP.md)

### Prompt injection surface (tracked: ROADMAP Day 6–7)
User-controlled goal strings are embedded in the LLM prompt without length capping
or structural delimiters. A carefully crafted goal or a document read by `files.read_text`
and used in a plan context could influence planner output.

**Mitigating factors:** output is validated for known skill IDs + JSON only; policy gates
and approval requirements catch unexpected execution patterns.

**Planned fix:** length cap on goal strings (2,000 chars), `[USER DATA: ...]` delimiters
in preamble to structurally separate system context from user input.

### Panel auth has no rate limiting (tracked: ROADMAP Day 4)
Repeated failed Basic auth attempts are logged but not rate-limited.
On a shared or remote host, the password endpoint is brute-forceable.

**Planned fix:** failed-attempt counter with 60-second lockout after 5 failures.
Lockout events logged as structured audit entries.

### No SIGTERM handler — crash or stop leaves jobs in ambiguous state (tracked: ROADMAP Day 4–5)
There is no signal handler in the queue daemon. On SIGTERM (systemd stop, kill) or an unhandled
crash, the job being processed is left in `pending/` with no failed-sidecar written.
The sidecar contract only applies to failures handled through the normal code path —
a mid-job termination bypasses it entirely.

On the next daemon start, the orphaned pending job will be re-picked up and re-executed.
For non-idempotent skills (file writes, app launches, clipboard) this means double execution.

**Operator note:** if the daemon was stopped or crashed mid-job, check `pending/` for jobs
that should have completed and inspect audit logs for the last recorded step before deciding
to retry, cancel, or manually move the job.

**Planned fix:** explicit SIGTERM handler that marks in-flight jobs as failed with
`reason=shutdown`, releases the queue lock, and exits cleanly within systemd's `TimeoutStopSec`.

### Artifact directories are not auto-pruned (tracked: ROADMAP Day 1)
`~/.voxera/artifacts/<job_id>/` directories accumulate without cleanup.
Failed-job retention pruning removes queue files but leaves artifact dirs behind.

**Planned fix:** tie artifact dir cleanup to the retention pruner;
add `voxera artifacts prune` CLI command.

### Brain fallback errors are unstructured (tracked: ROADMAP Day 2–3)
Fallback chain catches `except Exception` broadly.
The reason for fallback (timeout, auth, rate limit, malformed output) is not
classified or surfaced in a structured way, making postmortems harder.

**Planned fix:** classify into `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | UNKNOWN` enum;
log as structured JSON; surface in `voxera doctor` and health snapshots.

---

## Hardening backlog (ordered by priority)

1. **Goal string sanitization + length cap** — prompt injection defense layer.
2. **Panel auth rate limiting** — prevent brute force on operator password.
3. **Graceful SIGTERM handler** — prevent double-execution after daemon crash.
4. **Artifact directory auto-pruning** — prevent unbounded disk growth.
5. **LLM rate limiter** — prevent runaway planner calls from burning API quota.
6. **Eager skill manifest validation** — catch broken manifests at startup, not mid-job.
7. **Podman seccomp / AppArmor profiles** — tighten sandbox beyond `--read-only`.
8. **Signed skills + integrity verification** — prevent tampered skill entrypoints.
9. **Redaction pipeline for audit logs and telemetry** — strip PII and secrets from logs.
10. **Safe-mode boot** — limited skill set, no network, confirmation-only execution.

---

## For operators

- Run `voxera doctor` before starting the daemon to verify endpoint health and auth.
- Use `voxera queue health` for a quick lock/auth/counter snapshot during incidents.
- Panel mutations require `VOXERA_PANEL_OPERATOR_PASSWORD` — if not set, the panel shows a setup-required banner with no secrets displayed.
- Audit JSONL logs are at `~/.voxera/data/audit/`. Never delete these during incident triage.
- For incident response, use `voxera ops bundle system` and `voxera ops bundle job <job>` to capture a point-in-time snapshot.
- See `docs/ops.md` for the full incident runbook.
