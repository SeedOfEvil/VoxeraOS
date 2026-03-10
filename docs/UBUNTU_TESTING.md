# Ubuntu Testing Guide

Use this checklist to run Voxera OS Alpha v0.1.7+ (including GitHub PRs #145–#149) end-to-end on an Ubuntu machine.

## 1) System prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Optional (recommended for local model endpoint and audio/system skills):

```bash
sudo apt install -y curl jq pulseaudio-utils
```

## 2) Clone and enter the project

```bash
git clone <your-repo-url> VoxeraOS
cd VoxeraOS
```

## 3) Create environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

## 4) Run first-time setup

```bash
voxera setup
```

This writes local config/state files:
- `~/.config/voxera/config.yml` (app config: brain/mode/privacy settings)
- `~/.config/voxera/config.json` (runtime ops config: panel/queue settings; optional, create to override defaults)
- `~/.config/voxera/policy.yml`
- `~/.local/share/voxera/capabilities.json`
- `~/.local/share/voxera/audit/*.jsonl`

## 4b) Setup wizard STV (v0.1.7 guided brain flow)

1. Run `voxera setup`.
2. Confirm provider/model setup is sequential across brain slots: `primary`, `fast`, `reasoning`, `fallback`.
3. Select OpenRouter for at least one slot and verify vendor-grouped catalog flow (no giant raw table).
4. Verify recommended defaults are shown per slot: `primary=openai/gpt-4o-mini`, `fast=google/gemini-2.5-flash`, `reasoning=anthropic/claude-3.7-sonnet`, `fallback=meta-llama/llama-3.3-70b-instruct`.
5. Verify accepting the recommendation is easy and choosing an alternative vendor/model is also easy.
6. Optionally verify advanced manual model-id path.
7. Finish setup and verify explicit launch choices: Voxera panel / Vera panel / both / none.

Maintainer live-refresh check (optional):

```bash
python scripts/refresh_openrouter_catalog.py
```

## 5) Validate baseline behavior

```bash
voxera status
voxera skills list
voxera run system.status
```


## 5b) Run the guided demo (safe smoke check)

```bash
voxera demo
```

- Runs offline by default — no provider config required.
- Creates demo jobs (`demo-basic-*`, `demo-approval-*`) and validates queue + approval flows.
- Use `voxera demo --online` to additionally check provider readiness (missing keys remain `SKIPPED`, not failure).

## 5c) Install rootless Podman for sandbox skills

```bash
sudo apt install -y podman uidmap slirp4netns fuse-overlayfs
podman info --debug | head
```

On SELinux hosts, Podman bind mounts use `:Z` labels (Voxera applies this automatically).
On non-SELinux hosts the same mount option remains compatible.

## 6) Validate dry-run simulation (no execution)

```bash
voxera run system.set_volume --arg level=35 --dry-run
voxera run system.open_app --arg name=firefox --dry-run

# Execution boundary checks (PR3 hardening)
voxera run sandbox.exec --arg "command=['echo','ok']"
voxera run sandbox.exec --arg "command=echo ok && uname -a"   # should fail closed
voxera run files.write_text --arg path=demo.txt --arg text=ok
voxera run files.write_text --arg path=../escape.txt --arg text=nope  # should fail closed
```

Expected dry-run output is JSON with:
- `steps[]` including `policy_decision`, `requires_approval`, and `risk`
- `approvals_required`
- Runtime dispatch is fail-closed: missing/malformed/unknown capability metadata blocks step execution before invocation and should appear as `blocked` in artifacts.
- `blocked`
- `summary`

## 7) Run automated tests

```bash
pytest -q
```

## 8) (Optional) Run panel to inspect audit trail

```bash
voxera panel
```

Open `http://127.0.0.1:8844`.

Panel UI mutations (`/queue/create`, `/missions/create`) are POST-first. GET calls
are blocked by default with HTTP 405. If you need legacy GET mutation behavior for
CI/dev troubleshooting, start panel with:

```bash
VOXERA_PANEL_ENABLE_GET_MUTATIONS=1 voxera panel
```

## Troubleshooting

- If `voxera` command is not found, ensure your venv is activated.
- If setup cannot store secrets in keyring, Voxera OS falls back to file-based secret storage.
- If local-model tests fail, verify your endpoint is reachable and configured in `voxera setup`.


## 9) Queue observability + approval triage quick check

```bash
voxera queue status
voxera queue approvals list
```

Confirm `voxera queue status` includes:
- `failed metadata valid|invalid|missing`
- `failed retention max age (s)` and `failed retention max count`
- `Failed Retention (latest prune event)` with removed jobs/sidecars fields

If `failed metadata invalid` is non-zero, inspect malformed sidecars in:
- `~/VoxeraOS/notes/queue/failed/*.error.json`

Retention behavior is controlled by:
- `VOXERA_QUEUE_FAILED_MAX_AGE_S`
- `VOXERA_QUEUE_FAILED_MAX_COUNT`

## 10) Queue hygiene verification

```bash
# Dry-run preview — no changes made
voxera queue prune --max-age-days 30

# Report-only queue diagnostic
voxera queue reconcile
```

Expected output from `voxera queue prune` (dry-run): summary of jobs that *would* be pruned with
counts per bucket. No deletions without `--yes`.

Expected output from `voxera queue reconcile`: issue counts for orphan sidecars, orphan approvals,
artifact candidates, and duplicate jobs. Should show 0 issues on a clean queue.


- Verify representative built-in skills produce canonical `skill_result` keys (`summary`, `machine_payload`, `operator_note`, `next_action_hint`, `retryable`, `blocked`, `approval_status`, `error`, `error_class`) under success, invalid-input failure, and dependency-missing paths.
- Validate assistant read-only fast lane evidence:
  - enqueue an `/assistant` request and confirm `artifacts/<job>/execution_envelope.json` and `execution_result.json` both include lane metadata (`execution.lane`/`execution.fast_lane` and `execution_lane`/`fast_lane`).
  - confirm fast-lane-eligible advisory request shows `execution_lane=fast_read_only`.
  - confirm non-eligible advisory request shape (e.g. extra action hint or approval flag) remains `execution_lane=queue`.

## Manual STV: live progress UX (assistant + queue jobs)

1) Assistant advisory progress
- Open `/assistant`, submit a question.
- Observe request status/lifecycle move from queued/planning/advisory-running to done/failed if daemon is active.
- Verify no mission-step percent claims are shown for assistant jobs unless canonical step fields exist.

2) Normal read-only queue job progress
- Enqueue a deterministic read-only mission/goal.
- Open `/jobs/<job_id>` and confirm lifecycle + current/total step fields refresh without full page reload.

3) Approval-gated `open_url` progress
- Enqueue goal expected to require approval.
- Confirm live state reaches `awaiting_approval` with approval status `pending` and lane/intent metadata.
- Approve or deny and verify state transitions to terminal bucket.

4) Failed job progress
- Trigger a controlled failure case.
- Confirm `/jobs/<job_id>` shows terminal failed state plus stop reason/failure summary when emitted.

5) Final panel verification
- Disable JavaScript (or use a text browser) and confirm pages still render static detail correctly.
- Re-enable JavaScript and confirm live polling enhancement resumes.

## Security red-team regression gate (GitHub PR #147)

Run from repository root:

```bash
make security-check
```

What it validates (fast, deterministic):
- intent hijack/classifier abuse stays non-side-effecting,
- planner first-step skill-family mismatches fail closed,
- notes-root/path traversal-style phrasing does not get deterministic unsafe shortcuts,
- approval-gated jobs remain `awaiting_approval` until explicit approval,
- progress/evidence shaping does not leak stale failure context into succeeded views.

Interpretation:
- Any `security-check` failure is a trust-regression signal and should block merge until fixed or intentionally re-baselined with explicit review.
- This gate is hardening-only and should not be treated as product feature expansion.


### Manual STV for lineage metadata

1. Submit a normal queue job with no lineage metadata; verify behavior is unchanged and no lineage section is shown.
2. Submit a job with lineage keys (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`).
3. Verify successful execution and lineage visibility in `artifacts/<job>/execution_envelope.json`, `execution_result.json`, `plan.json`, `/jobs/<job>/progress`, and the panel job detail page.
4. Confirm no automatic child scheduling or dependency behavior occurs.


### Manual STV for controlled child enqueue (GitHub PR #149)

1. Submit a parent queue job with `enqueue_child.goal` and no parent lineage metadata.
2. Verify parent completes normally, exactly one `inbox/child-*.json` appears, and child lineage resolves to parent-root/depth+1/role=child.
3. Submit a parent queue job with lineage metadata and `enqueue_child`; verify child inherits root and increments depth.
4. Submit malformed `enqueue_child` payload (non-object, empty goal, extra keys); verify fail-closed behavior and no child job file.
5. Use child goal that requires approval (for example, open URL) and process next tick; verify child enters normal `pending/approvals` flow and parent did not bypass approvals.
6. Verify evidence surfaces: parent `child_job_refs.json`, parent `actions.jsonl` enqueue event, parent `execution_result.json` `child_refs` + `child_summary`, panel job detail `Child Jobs` + `Child Summary`, and progress `child_refs` + `child_summary`.

### Manual STV for child status rollup visibility

1. Create a parent that enqueues a simple read child.
2. Confirm parent shows `child_refs` and `child_summary` with `done=1`.
3. Create a parent that enqueues an `open_url` child.
4. Confirm parent `child_summary.awaiting_approval=1` while child approval is pending.
5. Approve the child; confirm parent summary moves to `done`/`succeeded` counts.
6. Confirm parent execution semantics are unchanged; summary is read-only observational metadata.


## Vera handoff smoke

1. Start daemon/panel and run `make vera` (defaults: `127.0.0.1:8790`).
2. In Vera, ask for an action like `open https://example.com`; confirm structured preview text and that nothing executed yet.
3. Explicitly hand off (`submit it` or UI submit button) and confirm `notes/queue/inbox/inbox-*.json` appears.
4. Confirm Vera reports submitted/queued and not yet executed; use queue/panel surfaces for runtime truth.

## Vera natural-language preview + handoff checks (PR #154)

Use this quick manual check after starting Vera (`make vera`):

- Ask `Can you go to example.com?` and verify Vera prepares a preview only.
- Verify DEV diagnostics include `preview_available: True`.
- Ask `submit it` and verify Vera reports submitted/queued (not executed yet).
- Verify a real queue job appears in `~/VoxeraOS/notes/queue/inbox/` and panel queue views.
- Repeat with: `visit example.com`, `take me to example.com`, `read ~/VoxeraOS/notes/test.txt`, and `make a note called hello.txt` (if enabled).
- Confirm informational asks (`what is example.com`, `tell me about example.com`) stay conversational and do not auto-open URLs.


## Vera evidence-aware outcome review checks (PR #155)

- Prepare + submit a Vera job, then wait until it is awaiting approval, succeeded, or failed.
- Ask Vera `what happened to that job?` and verify state/outcome language matches queue artifacts.
- Ask `did it work?` / `why did it fail?` and verify no invented execution claims appear when evidence is missing.
- Ask `what should I do next?` and verify next-step guidance is tied to canonical evidence.
- Ask `prepare the next step` and verify Vera drafts preview-only follow-up payload and does not auto-submit.


## PR #157 quick validation — structured write_file content

- Queue a payload with explicit `write_file.path` and `write_file.content` via Vera preview handoff or direct inbox JSON.
- Run daemon once and confirm file content is written exactly to requested filename.
- Verify `artifacts/<job>/execution_envelope.json` includes `request.write_file`.
- Verify `step_results.json` and `execution_result.json` show the real write target/result.


## Vera active preview draft replacement checks (PR #158)

- Prepare preview: `open example.com`; verify `preview_available: True`.
- Replace preview: `actually open openai.com instead`; verify updated preview is active.
- Add lightweight follow-up (`looks good`) and verify preview remains active.
- Submit (`submit it`) and verify inbox payload matches the updated preview.
- Repeat with file flow: create draft -> add content -> rename filename -> submit; confirm latest draft is submitted.
- Evidence flow: review prior job, ask `prepare the next step`, revise target, submit, verify latest revision reached queue.


## Manual STV: Vera authoritative preview pane + chat-first UX (PR #159)
1. Launch Voxera queue daemon, panel, and Vera web UI.
2. Ask for an action preview (`open example.com`) and verify the preview pane appears with active JSON draft.
3. Verify DEV diagnostics report `preview_available = True`.
4. Submit from pane (`Submit current preview to VoxeraOS`) and verify queue inbox job matches pane JSON exactly.
5. Prepare preview A, revise to preview B, verify pane now shows B only, then submit and verify payload equals B.
6. Use natural phrases (`that looks good now use it`, `use this preview`) and verify they submit only when active preview exists.
7. Verify no-preview case fails honestly and creates no job.
8. Verify successful submit clears preview pane affordance.
9. During repeated turns, verify chat view stays near latest messages automatically and conversation area has more usable space than prior layout.

## Optional: Vera as a user service

```bash
make services-install
make vera-status
make vera-logs
make vera-restart
make vera-stop
```

`make services-install` installs/enables `voxera-daemon.service`, `voxera-panel.service`, and `voxera-vera.service` in `~/.config/systemd/user/`.
