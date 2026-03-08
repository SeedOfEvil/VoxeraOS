# Ubuntu Testing Guide

Use this checklist to run Voxera OS Alpha v0.1.6 end-to-end on an Ubuntu machine.

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
