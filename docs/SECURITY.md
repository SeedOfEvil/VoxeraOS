# Security Posture and Hardening Notes

This document covers the VoxeraOS security model, threat analysis, and hardening details. For security vulnerability reporting, see [SECURITY.md](../SECURITY.md) in the repository root.

VoxeraOS is an open-source alpha project. Security is a core design principle — the system is built around fail-closed defaults, capability-gated execution, and evidence-backed outcomes — but alpha software should not be deployed in production security-critical environments.

---

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
| Intent hijack via meta/explanatory phrasing | High | ✅ Classifier rejects meta/help/why phrasing; fail-closed if first step mismatches allowed skill family (PR #144–#145) |
| Path traversal leakage through intent metadata | High | ✅ Traversal targets stripped at classifier, serializer, runtime, and sidecar boundaries (PR #147) |
| Panel auth brute force | Medium | ✅ Per-IP failed-auth lockout (10 attempts / 60s) with HTTP 429 + `Retry-After: 60` + audit/health surfaces (PR #89) |
| Mid-job daemon crash leaving ambiguous state | Medium | ✅ Graceful SIGTERM handler (PR #80) + deterministic startup recovery (PR #81) |
| Child enqueue bypassing approvals or policy | Medium | ✅ Child jobs enter normal queue lifecycle with full approval/policy/capability enforcement; server-side lineage prevents override (PR #149) |
| Lineage metadata widening authority | Low | ✅ Lineage fields are observational only; presence or absence does not change approvals, policy, scheduling, or execution (PR #148) |
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

This boundary control complements existing goal hardening: sanitize ASCII control characters, strip ANSI escape sequences only when ESC-prefixed, preserve benign bracketed text, and enforce a 2,000-character goal length cap before planner calls.

### Deterministic intent routing fail-closed guardrails (PR #144–#145)

Goal-kind queue jobs pass through a conservative deterministic classifier before reaching the planner:
- Classifies intent into one of: `open_terminal`, `open_url`, `open_app`, `write_file`, `read_file`, `run_command`, `assistant_question`, or `unknown_or_ambiguous`.
- Meta/help/explanatory phrasing (e.g. "tell me how to open terminal", "explain what open_url does") is explicitly excluded from action routing; such goals do not trigger execution.
- URL presence alone does **not** route a goal to `open_url`; the classifier requires explicit URL-open phrasing.
- Ambiguous or unrecognized open phrasing stays `unknown_or_ambiguous` and is not force-routed.
- If the planner's first step falls outside the intent's allowed skill family, the job fails closed **before any skill execution** with `stop_reason=planner_intent_route_rejected`.
- Compound requests (e.g. "open terminal and run X") have compound metadata preserved (`first_step_only`, `first_action_intent_kind`, `trailing_remainder`) so intent constraints apply to step 1 only.
- Terminal demo hijacks were removed in PR #145 to prevent canned-command injection through preamble shortcuts.

### Red-team regression suite and multi-boundary hardening (GitHub PR #147)

`tests/test_security_redteam.py` is a focused adversarial regression pack that runs as part of `make security-check`. It covers:
- **Intent hijack resistance**: meta/explanatory phrasing does not produce executable routing decisions.
- **Classifier compound smuggling**: multi-step goals with embedded action phrasing are limited to first-step constraint.
- **Ambiguous open phrase handling**: vague `"open an app"` phrasing stays `unknown_or_ambiguous`.
- **Planner mismatch fail-closed matrix**: multiple mismatch patterns all terminate before execution.
- **Planner mismatch is terminal**: confirmed `stop_reason=planner_intent_route_rejected`, no retry/bypass.
- **Safe notes-path read preserves extracted target**: legitimate file read paths are not blocked.
- **Traversal cases produce no extracted target**: paths containing `../` or other traversal patterns produce no `extracted_target` at any artifact boundary.
- **Traversal metadata omitted in all artifacts**: envelope, plan, sidecars, and state files do not leak traversal metadata.
- **Injected payload simple-intent sanitized**: serialization-boundary sanitization (`sanitize_serialized_intent_route()`) prevents unsafe intent fields from escaping into artifacts.
- **Approval-gated job remains pending**: `open_url` and similar goals requiring approval are never executed without explicit approval.
- **Progress surface avoids stale failure context**: terminal success views do not inherit stale failure summaries from earlier states.

Traversal leakage was specifically uncovered by this suite and fixed at four independent boundaries:
1. **Classifier boundary** — `_contains_parent_traversal()` guard prevents traversal-shaped phrasing from producing actionable routing metadata.
2. **Serializer boundary** — `sanitize_serialized_intent_route()` strips unsafe field values before they reach artifact serialization.
3. **Runtime boundary** — traversal targets are not surfaced in envelope, plan, or failed-sidecar artifacts.
4. **Sidecar boundary** — `_simple_intent` sanitized before writing to failed sidecar and state files.

`make security-check` is wired into `make validation-check` and `make merge-readiness-check`. Any failure in this suite is a trust-regression signal and should block merge until fixed with explicit review.

### Additive metadata does not widen authority (GitHub PR #148)

Queue lineage metadata (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, `lineage_role`) is observational only:
- Presence, absence, or specific values of lineage fields have **no effect** on approvals, policy decisions, capability grants, scheduling priority, or execution behavior.
- Lineage is not a trust signal: a job with `lineage_role=root` is not granted elevated authority; a job with `orchestration_depth=0` is not treated as a privileged root.
- Missing or malformed lineage values are sanitized and omitted without affecting execution.

### Controlled child enqueue safety semantics (GitHub PR #149)

The `enqueue_child` primitive is deliberately narrow and fail-closed:
- Exactly **one** child can be requested per parent execution; recursive or nested `enqueue_child` structures are rejected.
- Child enqueue is **explicit**, never inferred from skill outputs or job metadata.
- Child **lineage is computed server-side** from sanitized parent lineage; user-supplied lineage overrides inside the `enqueue_child` payload are stripped and ignored.
- The parent's approval gate is not transferred to the child: child jobs enter the normal queue lifecycle with their own full policy evaluation, approvals, capability checks, and fail-closed enforcement.
- Validation is strict: `enqueue_child` must be a plain object with only `goal` (required, non-empty string) and `title` (optional); extra keys, nested structures, and non-string goals are all rejected.
- No child is written if validation fails; there is no partial-enqueue or silent degradation.
- All evidence is auditable: `child_job_refs.json`, `actions.jsonl` (`queue_child_enqueued` event), `execution_result.json` (`child_refs`), job progress, and panel job detail all surface child relationships.

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
- String values that include shell-control operators (`&&`, `;`, pipes, redirects) are rejected as ambiguous unless an explicit shell argv is provided.
- List values: all elements must be strings and must not be empty/whitespace-only.
- If argv is empty/missing/ambiguous or contains invalid tokens, execution fails fast with a clear error and canonical `skill_result` (`error_class=invalid_input`):
  `"sandbox.exec command must be a non-empty list of strings. Provide args.command as a list like ['bash','-lc','echo hello'] or a non-empty string."`
- `shell=True` is never used; the argv list is passed directly to Podman.

#### Confined file path normalization (`normalize_confined_path`)
- `files.read_text`, `files.write_text`, `files.list_dir`, `files.find`, `files.grep_text`, `files.list_tree`, `files.copy_file`, `files.move_file`, `files.copy`, `files.move`, `files.rename`, `files.mkdir`, `files.exists`, `files.stat`, and `files.delete_file` share centralized path-boundary enforcement in `src/voxera/skills/path_boundaries.py`.
- Relative paths are rooted to `~/VoxeraOS/notes`, then normalized deterministically.
- Control-plane queue paths (`~/VoxeraOS/notes/queue/**`) are explicitly blocked from file-skill access (`error_class=path_blocked_scope`).
- Traversal attempts (`..`), absolute out-of-root paths, and symlink escapes are blocked (`error_class=path_out_of_bounds`).
- Invalid path forms (empty/null-byte) are rejected (`error_class=invalid_path`).

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

## Previously tracked gaps (now resolved)

### Planner goal-string hardening (FIXED — PR #85 + PR #88)
- Goal inputs are rejected when longer than 2,000 characters before any planner brain call.
- Goal text embedded in planner prompts is sanitized by stripping ASCII control chars (`0x00-0x1F`, `0x7F`).
- Prompt embedding normalizes whitespace (collapse runs + trim ends) so planner sees stable user input.
- User goal text is structurally isolated with `[USER DATA START]`/`[USER DATA END]` delimiters so the model treats it as untrusted input, not system instructions.

### Intent routing fail-closed enforcement (FIXED — PR #144–#145)
- Deterministic classifier guards all goal-kind jobs; mismatch between planner first step and allowed skill family terminates the job before any execution.
- Meta/explanatory phrasing explicitly excluded from actionable routing.
- Narrow conservative open-intent splits; fail-closed on ambiguity.

### Traversal metadata leakage (FIXED — PR #147)
- Traversal-shaped goals no longer produce deterministic extracted targets at classifier, serializer, runtime, or sidecar boundaries.
- Red-team suite now verifies all four boundaries as merge-blocking tests.

---

## Hardening backlog (ordered by priority)

1. **LLM rate limiter** — prevent runaway planner calls from burning API quota (P6.2, v0.2).
2. **Eager skill manifest validation** — catch broken manifests at startup, not mid-job (P6.1, v0.2).
3. **Podman seccomp / AppArmor profiles** — tighten sandbox beyond `--read-only`.
4. **Signed skills + integrity verification** — prevent tampered skill entrypoints (v0.4).
5. **Redaction pipeline for audit logs and telemetry** — strip PII and secrets from logs.
6. **Safe-mode boot** — limited skill set, no network, confirmation-only execution (v0.4).

Previously tracked items now resolved:
- ~~Goal string sanitization + length cap~~ — FIXED in PR #85 (2,000-char cap + control-char stripping).
- ~~Structural prompt injection delimiters~~ — FIXED in PR #88 (`[USER DATA START]`/`[USER DATA END]`).
- ~~Panel auth rate limiting~~ — FIXED in PR #89 (10/60s → HTTP 429 + `Retry-After: 60`).
- ~~Graceful SIGTERM handler~~ — FIXED in PR #80–#81 (graceful shutdown + startup recovery).
- ~~Artifact directory auto-pruning~~ — FIXED in v0.1.5 (`voxera artifacts prune`) + v0.1.6 (`voxera queue prune`).
- ~~Brain fallback errors unstructured~~ — FIXED in PR #73 (`BrainFallbackReason` enum).
- ~~Intent hijack via meta/explanatory phrasing~~ — FIXED in PR #144–#145 (classifier guards + fail-closed mismatch detection).
- ~~Path traversal leakage through intent metadata~~ — FIXED in PR #147 (multi-boundary hardening: classifier, serializer, runtime, sidecar).
- ~~Health degradation state tracking~~ — SHIPPED (P3.1: `consecutive_brain_failures`, `daemon_state=degraded` tracking implemented).
- ~~Brain planning backoff enforcement~~ — SHIPPED (P3.2: `compute_brain_backoff_s()` ladder with env knobs and `health.json` reporting).

---

## For operators

- Run `voxera doctor` before starting the daemon to verify endpoint health and auth.
- Use `voxera queue health` for a sectioned incident snapshot: Current State, Recent History, and Historical Counters; use `--watch` for live refresh and `--json` for parity keys (`current_state`, `recent_history`, `counters`).
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
