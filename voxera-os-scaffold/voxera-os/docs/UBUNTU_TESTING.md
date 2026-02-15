# Ubuntu Testing Guide

Use this checklist to run Voxera OS Alpha v0.1.3 end-to-end on an Ubuntu machine.

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
cd VoxeraOS/voxera-os-scaffold/voxera-os
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
- `~/.config/voxera/config.yml`
- `~/.config/voxera/policy.yml`
- `~/.local/share/voxera/capabilities.json`
- `~/.local/share/voxera/audit/*.jsonl`

## 5) Validate baseline behavior

```bash
voxera status
voxera skills list
voxera run system.status
```


## 5b) Install rootless Podman for sandbox skills

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
```

Expected dry-run output is JSON with:
- `steps[]` including `policy_decision`, `requires_approval`, and `risk`
- `approvals_required`
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

## Troubleshooting

- If `voxera` command is not found, ensure your venv is activated.
- If setup cannot store secrets in keyring, Voxera OS falls back to file-based secret storage.
- If local-model tests fail, verify your endpoint is reachable and configured in `voxera setup`.
