# Runtime Validation Playbook

This playbook turns VoxeraOS runtime testing into a repeatable operator workflow.

Use it when validating a local branch, a PR, or release-candidate behavior across the real trust layers:

- CLI/operator surfaces
- queue + daemon runtime
- artifact/evidence contract
- panel truth rendering
- Vera web draft/save/submit handoff
- actual filesystem/runtime outcomes

## 1) Purpose

VoxeraOS is only healthy when all runtime layers tell the same story.

A green unit test or a plausible UI message is not enough. Runtime validation must verify end-to-end truth from input to queue lifecycle to artifacts to user-facing surfaces.

## 2) Core rule (trust model)

Do not trust a feature until it has been tested across the layers that make it real.

- **Vera** = reasoning and drafting layer
- **queue** = execution boundary
- **artifacts** = proof of what actually happened

If any layer disagrees, trust canonical queue/artifact evidence first and treat UI/chat output as suspect until reconciled.

## 3) Validation order (run in this sequence)

Run validation in the same order every time:

1. Sync branch and install
2. Run static validation gate
3. Restart runtime services and confirm health
4. Test queue happy path
5. Test blocked boundary path
6. Test approval-paused path
7. Test retryable non-boundary failure path
8. Test panel truth against those jobs
9. Test Vera smoke flows (save + submit)
10. Confirm artifacts/evidence match queue and UI truth

This order catches regressions quickly and prevents chasing stale runtime state.

## 4) Sync + install

```bash
cd ~/VoxeraOS
git fetch --all --prune
git status
git rebase origin/main
source .venv/bin/activate
pip install -e ".[dev]"
```

If this branch depends on fresh generated docs/goldens, refresh before running gates.

## 5) Static validation gate (must pass first)

```bash
ruff format --check .
ruff check .
mypy src/voxera
pytest -q tests/test_docs_consistency.py tests/test_cli_version.py tests/test_panel.py
make merge-readiness-check
```

If static gates fail, stop and fix before runtime smoke work.

## 6) Runtime bring-up + health baseline

Restart services:

```bash
make daemon-restart
systemctl --user restart voxera-panel.service
make vera-restart
make daemon-status
systemctl --user --no-pager status voxera-panel.service
make vera-status
```

Then confirm operator health:

```bash
voxera doctor --quick
voxera queue status
voxera queue health
voxera queue approvals list
```

Expected healthy baseline:

- daemon service reachable
- queue buckets readable
- no unexplained growth in `failed metadata invalid`
- no stale lock warning

## 7) Queue reset / cleanup (when needed)

Use reset only when previous runs pollute current validation:

```bash
voxera queue health-reset --scope current_and_recent
voxera queue reconcile --json
voxera queue prune --max-age-days 14
voxera artifacts prune --max-age-days 14
```

For deterministic runtime scenario runs, clear only scenario IDs/artifacts you own (do not mass-delete shared queue evidence on multi-user hosts).

## 8) CLI/operator sanity checks

```bash
voxera --version
voxera skills list
voxera missions list
voxera queue status
voxera queue health --json | jq '.current_state,.recent_history,.counters'
```

This verifies the CLI/operator layer is alive before deeper runtime assertions.

## 9) Standard runtime scenarios

Payload fixtures are provided in `docs/testing/payloads/`.

## 9.1 Stage fixtures into queue inbox

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"

cp docs/testing/payloads/system-inspect.json "$QUEUE_ROOT/inbox/rvp-system-inspect.json"
cp docs/testing/payloads/blocked-list-dir.json "$QUEUE_ROOT/inbox/rvp-blocked-list-dir.json"
cp docs/testing/payloads/delete-approval-test.json "$QUEUE_ROOT/inbox/rvp-delete-approval.json"
cp docs/testing/payloads/missing-source-copy.json "$QUEUE_ROOT/inbox/rvp-missing-source-copy.json"
cp docs/testing/payloads/inline-exists-test.json "$QUEUE_ROOT/inbox/rvp-inline-exists.json"
```

## 9.1b Preferred operator path: queue filesystem helpers

For filesystem runtime checks, prefer CLI helpers over manual JSON crafting:

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"

voxera queue files find --root-path "$HOME/VoxeraOS/notes/runtime-validation" --glob "*.md" --id rvp-files-find --queue-dir "$QUEUE_ROOT"
voxera queue files grep --root-path "$HOME/VoxeraOS/notes" --pattern "runtime" --id rvp-files-grep --queue-dir "$QUEUE_ROOT"
voxera queue files tree --root-path "$HOME/VoxeraOS/notes/runtime-validation" --id rvp-files-tree --queue-dir "$QUEUE_ROOT"

voxera queue files copy --source-path "$HOME/VoxeraOS/notes/runtime-validation/source.txt" --destination-path "$HOME/VoxeraOS/notes/runtime-validation/copied.txt" --id rvp-files-copy --queue-dir "$QUEUE_ROOT"
voxera queue files move --source-path "$HOME/VoxeraOS/notes/runtime-validation/copied.txt" --destination-path "$HOME/VoxeraOS/notes/runtime-validation/moved.txt" --id rvp-files-move --queue-dir "$QUEUE_ROOT"
voxera queue files rename --path "$HOME/VoxeraOS/notes/runtime-validation/moved.txt" --new-name renamed.txt --id rvp-files-rename --queue-dir "$QUEUE_ROOT"
```

Each command only enqueues. It does not claim execution success until daemon processing and artifact evidence confirm outcome.

Process queue (daemon loop or one-shot):

```bash
voxera daemon --once
# or let systemd daemon drain inbox continuously
```

Track status:

```bash
voxera queue status
voxera queue approvals list
```

## 9.2 Scenario A: happy path success (`system_inspect`)

Fixture: `system-inspect.json`

What it proves:

- mission execution path is healthy
- done-state and artifact minimum contract hold

Expected pass:

- job lands in `done/`
- `execution_result.json` terminal outcome is successful/succeeded
- minimum artifacts present

## 9.3 Scenario B: blocked boundary failure (`files.list_dir` against queue root)

Fixture: `blocked-list-dir.json`

What it proves:

- capability/path boundary hardening is fail-closed
- blocked semantics are truthful (not misreported as generic crash)

Expected pass:

- job lands in `failed/` with blocked/policy-denied semantics
- step/result evidence indicates boundary denial
- panel shows blocked/fail-closed truth, not fake success

## 9.4 Scenario C: approval-paused path (`files.delete_file`)

Fixture: `delete-approval-test.json`

Setup helper:

```bash
mkdir -p ~/VoxeraOS/notes/runtime-validation
echo "delete me" > ~/VoxeraOS/notes/runtime-validation/delete-me.txt
```

What it proves:

- approval gate pauses execution before destructive action
- queue/panel show awaiting-approval state truthfully

Expected pass:

- job reaches `awaiting_approval` / pending approvals
- no fake terminal success before approve/deny decision
- approve path resumes and reaches terminal state; deny path records denied failure truthfully

## 9.5 Scenario D: retryable non-boundary failure (`file_organize` missing source)

Fixture: `missing-source-copy.json`

What it proves:

- ordinary runtime/data failure is surfaced honestly
- failure is not mislabeled as boundary/policy block

Expected pass:

- job lands in `failed/`
- failure summary indicates missing source/input problem
- semantics remain runtime failure, not blocked boundary denial

## 9.6 Scenario E: inline steps happy path (`files.exists`)

Fixture: `inline-exists-test.json`

What it proves:

- inline step normalization + execution contracts are healthy
- simple deterministic skill results flow through queue and artifacts

Expected pass:

- terminal state is success/done
- step results include `files.exists` payload and truthful existence result

## 9.7 Scenario F: filesystem helper blocked boundary (`queue files tree`)

What it proves:

- operator CLI helper still respects policy boundaries
- blocked result remains blocked (not generic failure)

Example:

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"
voxera queue files tree --root-path "$QUEUE_ROOT" --id rvp-files-blocked --queue-dir "$QUEUE_ROOT"
voxera daemon --once
```

Expected pass:

- job lands in `failed/` with blocked semantics
- `execution_result.json` indicates blocked terminal outcome
- step result marks `blocked=true` with policy reason class

## 9.8 Scenario G: filesystem helper missing source (`queue files copy`)

What it proves:

- missing inputs remain normal runtime failures
- failures are not mislabeled as blocked scope

Example:

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"
voxera queue files copy --source-path "$HOME/VoxeraOS/notes/runtime-validation/missing.txt" --destination-path "$HOME/VoxeraOS/notes/runtime-validation/should-not-exist.txt" --id rvp-files-missing --queue-dir "$QUEUE_ROOT"
voxera daemon --once
```

Expected pass:

- job lands in `failed/`
- `execution_result.json` reports terminal failure
- step result `blocked=false` with missing-source/not-found class evidence

## 9.9 Scenario H: mutating control-plane scope is fail-closed blocked (`queue files move`)

Goal:
- verify governed mutating file actions treat queue/control-plane scope as blocked boundary
- ensure operator surfaces show truthful blocked metadata (not generic failed)

Command:

```bash
voxera queue files move --source-path "$HOME/VoxeraOS/notes/runtime-validation/source.txt" --destination-path "$HOME/VoxeraOS/notes/queue/blocked.txt" --id rvp-files-move-blocked --queue-dir "$QUEUE_ROOT"
```

Expected:
- job lands in `failed/` with blocked boundary semantics
- `execution_result.json` has `blocked=true` and `blocked_reason_class=path_blocked_scope`
- `step_results.json` records the attempted step with boundary class and blocked outcome hints

## 10) Raw queue JSON helpers

Create ad-hoc payload quickly:

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"
cat > "$QUEUE_ROOT/inbox/rvp-ad-hoc.json" <<'JSON'
{
  "id": "rvp-ad-hoc",
  "steps": [
    {"skill_id": "files.exists", "args": {"path": "~/VoxeraOS/notes/runtime-validation/delete-me.txt"}}
  ],
  "goal": "ad hoc runtime check"
}
JSON
```

Watch lifecycle sidecars quickly:

```bash
watch -n 1 "voxera queue status"
```

## 11) Artifact inspection (canonical truth)

For a job ID, inspect:

```bash
JOB_ID="rvp-system-inspect"
ART_DIR="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/artifacts/$JOB_ID"

ls -la "$ART_DIR"
jq . "$ART_DIR/execution_envelope.json"
jq . "$ART_DIR/execution_result.json"
jq . "$ART_DIR/step_results.json"
jq . "$ART_DIR/job_intent.json"
jq . "$ART_DIR/plan.json"
tail -n 50 "$ART_DIR/actions.jsonl"
```

If failed/approval cases apply, also inspect:

```bash
QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}"
ls -la "$QUEUE_ROOT/pending/approvals" | rg "rvp-"
ls -la "$QUEUE_ROOT/failed" | rg "rvp-"
```

## 12) Panel checks (truth reconciliation)

Open panel and verify for each scenario job:

- lifecycle state matches queue bucket/state sidecar
- terminal outcome matches `execution_result.json`
- step/result summary aligns with `step_results.json`
- approval job is shown as awaiting approval until decision
- blocked job reflects blocked boundary semantics

Panel is a consumer of canonical truth; if panel and artifacts disagree, treat that as a regression.

## 13) Vera web smoke flows (save + submit)

Use these as manual smoke prompts (not hardcoded behavior requirements):

1. **Code save + submit**
   - “Write me a python script that fetches a URL and prints the page title.
     save it
     submit it”
2. **Explanation save + submit**
   - “Explain photosynthesis simply.
     thanks
     put your previous explanation in a note called photosynthesis.txt
     submit it”
3. **Investigation save + submit**
   - “Search the web for the latest official Brave Search API documentation
     summarize all findings
     save that as brave-api-summary.md
     submit it”
4. **Writing save + submit**
   - “Tell me about black holes.
     Write a 2 page essay about that.
     save it as black-holes-essay.md
     submit it”

Expected pass:

- Vera drafts/saves in preview space first
- explicit submit creates queue inbox payload/job
- queue/daemon own execution lifecycle and evidence
- no direct execution bypass from Vera chat path

## 14) Negative-path checks (fail-closed)

Always include at least one fail-closed check per run:

- blocked boundary payload (`blocked-list-dir.json`)
- approval gate pause (`delete-approval-test.json`)
- non-boundary runtime failure (`missing-source-copy.json`)

The system should fail clearly, deterministically, and honestly.

## 15) What counts as a real pass

A runtime validation pass requires **all** of the following:

1. Static gates are green.
2. Runtime services are healthy.
3. Standard scenarios produce expected lifecycle outcomes.
4. Artifact contract is present and coherent.
5. Panel truth matches queue + artifacts.
6. Vera save/submit flow hands off through queue (no bypass).
7. Filesystem/runtime side effects (or non-side-effects) match the declared outcome.

If any layer disagrees, mark validation as failed and file an evidence-backed regression.

## 16) Quick triage command bundle

```bash
voxera queue status
voxera queue health
voxera queue approvals list
ls -la "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/inbox" | tail -n 20
ls -la "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/pending" | tail -n 20
ls -la "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/done" | tail -n 20
ls -la "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/failed" | tail -n 20
rg -n "rvp-" "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}" -g "*.json"
```

Use this bundle in PR validation notes so other contributors can reproduce quickly.
