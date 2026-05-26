# V1 Release Candidate Checklist (PR119)

## 1. Purpose

This document is the authoritative V1 release-candidate gate for ShellForgeAI. A build is not called V1-ready until this checklist is completed and evidence artifacts are attached.

## 2. V1 promise

ShellForgeAI V1 is a **CLI-first Linux/Docker operator knife**:

- Produces evidence-backed operator reports.
- Preserves, exports, and compares report artifacts.
- Routes common operator asks deterministically.
- Refuses mutation asks in normal conversational/operator paths.
- Allows governed mutation only through explicit guarded lanes outside the casual V1 path.

## 3. What V1 includes

- `doctor` / `model doctor`
- `v1 check`
- `v1 validate` helper (`scripts/v1_validate.sh`)
- `ops report`
- `ops report save/validate/export/history/compare/compare-latest`
- `triage docker` / `triage detail`
- `remediation eligibility` / `remediation explain`
- deterministic ask-to-report routing
- deterministic mutation refusal
- V1 packet `save/validate/export/history/compare`

## 4. What V1 does not include

- production autonomous remediation
- broad Docker/Compose mutation
- arbitrary shell execution
- web UI
- secrets platform expansion
- production cleanup execution by default
- natural-language mutation execution

## 5. Required local/dev validation

Run all commands from repo root:

```bash
ruff check .
python -m compileall -q src tests
pytest -q
./scripts/v1_validate.sh --quick
./scripts/v1_validate.sh --full
./scripts/v1_validate.sh --quick --packet
./scripts/v1_validate.sh --quick --export-packet
```

## 6. Required Docker01 smoke validation

```bash
shellforgeai version
shellforgeai doctor
shellforgeai model doctor
shellforgeai v1 check --profile quick --json
shellforgeai v1 check --profile standard --json
shellforgeai ops report --json
shellforgeai remediation self-test --profile full --json
```

## 7. Required deterministic ask validation

```bash
shellforgeai ask "It's 2AM; what is on fire?"
shellforgeai ask "please restart shellforgeai"
```

Expected:

- First ask deterministically routes to read-only ops/triage report output.
- Second ask deterministically refuses mutation execution.

## 8. Required artifact validation

```bash
shellforgeai ops report --save --json
shellforgeai ops report validate <report-id> --json
shellforgeai ops report export <report-id> --json
shellforgeai ops report export-validate <export-id> --json
shellforgeai ops report history --limit 5 --json
shellforgeai ops report compare-latest --json
shellforgeai v1 packet --save --json
shellforgeai v1 packet validate <packet-id> --json
shellforgeai v1 packet export <packet-id> --json
shellforgeai v1 packet export-validate <export-id> --json
```

## 9. Safety invariants

All normal V1 validation paths must show:

- no remediation execute
- no rollback execute
- no cleanup execute
- no Docker Compose mutation
- no production restart
- no `shell=True`
- no arbitrary command execution
- no natural-language mutation

## 10. Known acceptable caveats

- Historical metadata hygiene warnings may appear in long-lived labs.
- Validation containers must include expected tools (for example: `procps`, `git`, `rsync`, dev dependencies).
- Runtime image may not include `pytest`/`ruff`.
- Battle-lab fixtures may remain intentionally broken.

## 11. Hard blockers

Any of the below is a release blocker:

- `shellforgeai version` fails
- `v1 check` fails
- ops report JSON is invalid/unparseable
- mutation ask executes action instead of refusing
- production ShellForgeAI restarts unexpectedly
- cleanup/remediation/rollback executes without explicit governed gates
- full `pytest -q` fails
- packet/export validation fails

## 12. Docker01 handoff template

Copy/paste and fill:

```text
PR: #119
Commit: <commit-sha>
Image: <image-tag-or-digest>
Snapshot: <snapshot-id-or-timestamp>

Source/Image/Label verification:
- source commit verified: <yes/no>
- image label verified: <yes/no>
- runtime label verified: <yes/no>

Smoke:
- shellforgeai version: <pass/fail>
- shellforgeai doctor: <pass/fail>
- shellforgeai model doctor: <pass/fail>

Validation:
- v1 validate quick/full: <pass/fail>
- pytest/ruff/compileall: <pass/fail>

V1 checks:
- v1 check quick/standard: <pass/fail>
- deterministic ask route/refusal: <pass/fail>

Artifact checks:
- report save/validate/export/export-validate: <pass/fail>
- report history/compare-latest: <pass/fail>
- packet save/validate/export/export-validate: <pass/fail>

Safety checks:
- no cleanup execute observed: <yes/no>
- no remediation execute observed: <yes/no>
- no rollback execute observed: <yes/no>
- no docker/compose mutation observed: <yes/no>
- no production restart observed: <yes/no>

Caveats:
- <none or list>

Verdict:
- <V1-ready | V1-ready with caveats | V1-blocked>
```

## 13. Final V1 release sign-off

Select one:

- **V1-ready**
- **V1-ready with caveats**
- **V1-blocked**

Sign-off:

- Operator: __________________
- Signature: _________________
- Date (UTC): ________________

## Rollback / recovery note

If release-candidate validation regresses after merge-candidate generation, mark as **V1-blocked**, preserve collected artifacts/packets as evidence, and roll back to the last known-good commit and image snapshot before reopening release gating.
