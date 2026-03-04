## OpenRouter attribution headers

When using OpenRouter via the OpenAI-compatible adapter, VoxeraOS sends `HTTP-Referer` and title headers (`X-OpenRouter-Title` plus compatibility `X-Title`) as app attribution metadata. These values are non-secret metadata and are not API credentials.

# Security & Safety

## Threat model

| Threat | Risk | Mitigated? |
|---|---|---|
| Accidental destructive actions (rm, installs, firewall changes) | High | ✅ Policy gates + approval workflow |
| Prompt injection via user-controlled content | High | ✅ Goal strings sanitized + 2,000-char cap before planning (PR #85) + `[USER DATA START]`/`[USER DATA END]` prompt boundaries (PR #88) |
| Secret leakage (API keys, tokens) | High | ✅ Keyring + 0600 fallback; redacted in config show/snapshot |
| Over-permissioned skills | High | ✅ Capability declarations + policy engine |
| Panel auth brute force | Medium | ✅ Per-IP failed-auth lockout (10 attempts / 60s) with HTTP 429 + `Retry-After: 60` + audit/health surfaces |
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

### Planner Prompt Boundaries
User-controlled planner fields are wrapped with explicit delimiters:
- `[USER DATA START]`
- `[USER DATA END]`

Planner instructions require the model to treat everything inside this bounded region as **untrusted user data** and to never follow instructions found there.

This boundary control complements existing goal hardening: sanitize control/ANSI characters and enforce a 2,000-character goal length cap before planner calls.

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

#### sandbox.exec command arg validation (`canonicalize_argv`)
All `sandbox.exec` command arguments are normalised through `canonicalize_argv` before execution:
- Accepted keys (priority order): `command`, `argv`, `cmd`.
- String values are tokenised with `shlex.split` (no implicit shell wrapper).
- List values: all elements must be strings; empty/whitespace-only tokens are silently stripped.
- If the final argv is empty, missing, or contains non-string tokens, execution fails fast with a clear error:
  `"sandbox.exec command must be a non-empty list of strings. Provide args.command as a list like ['bash','-lc','echo hello'] or a non-empty string."`
- `shell=True` is never used; the argv list is passed directly to Podman.

Canonical best-practice format:
```json
{"skill_id": "sandbox.exec", "args": {"command": ["bash", "-lc", "echo hello"]}}
```
Non-shell direct exec (no bash wrapper needed):
```json
{"skill_id": "sandbox.exec", "args": {"command": ["ip", "a"]}}
```

### Panel daemon health widget data source
Panel home (`/`) now includes a collapsible **Daemon Health** widget that reads only the local
health snapshot file (`notes/queue/health.json`) through shared health-loader utilities.

Security and deployment implications:
- No direct daemon RPC or background daemon call is performed by the panel for this widget.
- The widget is read-only and reflects persisted snapshot data only.
- Panel-only deployments remain safe/supported: if the daemon is not running, neutral placeholders
  are shown without failing requests.

### Panel auth rate limiting
Panel Basic-auth failures are tracked per client IP in `health.json` under `panel_auth`:
- `failures_by_ip`: rolling failure counters (`count`, `first_ts_ms`, `last_ts_ms`)
- `lockouts_by_ip`: lockout windows (`until_ts_ms`, `count`, `last_event_ts_ms`)

Policy:
- `FAIL_THRESHOLD = 10` failed attempts
- `WINDOW_S = 60` seconds
- `LOCKOUT_S = 60` seconds

When an IP crosses the threshold inside the window, panel auth returns HTTP `429` and sets
`Retry-After: 60`. Lockout events are emitted as structured audit records (`panel_auth_lockout`)
with `ip`, `attempt_count`, `window_s`, and `lockout_s`.

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

Shutdown context is persisted in `health.json` (`last_shutdown_outcome`, `last_shutdown_ts`,
`last_shutdown_reason`, `last_shutdown_job`) and surfaced read-only in queue/doctor/panel.
Treat `last_shutdown_reason` as operator-facing diagnostic text and avoid embedding secrets in
exception messages.

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

### Planner goal-string hardening (FIXED — PR #85 + PR #88)
- Goal inputs are rejected when longer than 2,000 characters before any planner brain call.
- Goal text embedded in planner prompts is sanitized by stripping ASCII control chars (`0x00-0x1F`, `0x7F`).
- Prompt embedding normalizes whitespace (collapse runs + trim ends) so planner sees stable user input.
- User goal text is structurally isolated with `[USER DATA START]`/`[USER DATA END]` delimiters so the model treats it as untrusted input, not system instructions.


---

## Hardening backlog (ordered by priority)

1. **LLM rate limiter** — prevent runaway planner calls from burning API quota (P6.2, v0.2).
2. **Eager skill manifest validation** — catch broken manifests at startup, not mid-job (P6.1, v0.2).
3. **Health degradation state tracking** — surface `daemon_state=degraded` after consecutive failures (P3.1, v0.2).
4. **Brain planning backoff enforcement** — daemon applies bounded sleep before planning on repeated brain failures; latest applied delay is recorded in `health.json` (`brain_backoff_last_applied_s`, `brain_backoff_last_applied_ts`).
5. **Podman seccomp / AppArmor profiles** — tighten sandbox beyond `--read-only`.
6. **Signed skills + integrity verification** — prevent tampered skill entrypoints (v0.4).
7. **Redaction pipeline for audit logs and telemetry** — strip PII and secrets from logs.
8. **Safe-mode boot** — limited skill set, no network, confirmation-only execution (v0.4).

Previously tracked items now resolved:
- ~~Goal string sanitization + length cap~~ — FIXED in PR #85 (2,000-char cap + control-char stripping).
- ~~Structural prompt injection delimiters~~ — FIXED in PR #88 (`[USER DATA START]`/`[USER DATA END]`).
- ~~Panel auth rate limiting~~ — FIXED in PR #89 (10/60s → HTTP 429 + `Retry-After: 60`).
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


## Panel hygiene triggers safety model

The panel `/hygiene` actions are intentionally constrained:

- They invoke local CLI subprocess commands (`voxera queue prune --json` (dry-run by default, no `--yes`) and `voxera queue reconcile --json`) rather than daemon RPC calls.
- Prune is forced to dry-run/report mode only in panel flow; no deletion is performed.
- Reconcile endpoint runs report-only analysis (`--json` without fix/apply flags).
- Results are persisted into `notes/queue/health.json` (`last_prune_result`, `last_reconcile_result`) using the same atomic health snapshot write path used elsewhere.
- POST triggers are mutation-guarded by operator auth (+ CSRF when enabled).


### Panel recovery/quarantine inspector safety model

The panel `/recovery` surface is intentionally read-only and panel-only safe:

- No daemon RPC calls.
- No queue mutations (no delete/move/write of queue content).
- Data source is filesystem listing only under:
  - `notes/queue/recovery/`
  - `notes/queue/quarantine/`

ZIP downloads (`/recovery/download/{bucket}/{name}`) enforce:

- Strict bucket allowlist: `recovery` or `quarantine`.
- `name` must be exactly one path segment (no slash/backslash traversal).
- Resolved target path must remain under bucket root (`Path.resolve()` + containment check).
- Symlinks are excluded from listings and skipped during ZIP construction.
- Archive bounds to reduce abuse risk: max 5,000 files and 250MB source payload per ZIP.

This provides operator access to recovery artifacts without introducing queue mutation risk.
