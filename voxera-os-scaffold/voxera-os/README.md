# Voxera OS (Scaffold) — Voice-first AI Control Plane

Voxera OS is an **AI-controlled OS experience** built as a reliable *control plane* on top of a standard Linux substrate.
This repo is a **first-commit scaffold**: it ships a typed first-run setup (`voxera setup`), a tool/skill runner,
a minimal approval/audit web panel, and pluggable “brain” providers (cloud or local OpenAI-compatible endpoints).

**Names**
- OS: **Voxera OS**
- Core AI persona: **Vera**
- Wake word (planned): **“Hey Voxera”**
- CLI: `voxera`

## What works in this scaffold
- ✅ Typed setup wizard (TUI) to pick **Local vs Cloud** brain + store config safely
- ✅ OpenRouter-first cloud setup path with recommended headers + model tiers (fast/balanced/reasoning/fallback)
- ✅ Provider abstraction layer + adapters (OpenAI-compatible works immediately with local servers like Ollama/OpenRouter)
- ✅ Skill registry + permissions + approval gating (MVP)
- ✅ Audit log (JSONL) + rollback hooks (MVP)
- ✅ Minimal panel (FastAPI) to review approvals + audit trail

## Quick start (dev VM)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

### 1) Run setup
```bash
voxera setup
```
This writes config to:
- `~/.config/voxera/config.yml`
- secrets stored via `keyring` when possible; otherwise a 0600 fallback file


### OpenRouter recommended setup
`voxera setup` now includes an **OpenRouter** cloud option and configures:
- Base URL: `https://openrouter.ai/api/v1`
- Headers: `HTTP-Referer` and `X-Title`
- Brain tiers: `primary`, `fast`, `reasoning`, `fallback`

After setup, run:
```bash
voxera doctor
```
to verify each configured model endpoint.

### 2) Try basic commands
```bash
voxera status
voxera skills list
voxera run system.status
voxera run system.open_app --arg name=firefox --dry-run
```

### 2b) Try built-in missions (agent-style multi-step flow)
```bash
voxera missions list
voxera missions run system_check --dry-run
voxera missions run work_mode
```

### 2c) Let cloud AI plan a mission from a goal
```bash
voxera missions plan "prep a focused work session" --dry-run
voxera missions plan "run a quick health check and open my terminal"
```
This uses your configured `primary` brain provider and still enforces local policy + approvals.


### 2d) Queue missions for daemon execution
```bash
mkdir -p ~/VoxeraOS/notes/queue
echo '{"mission_id":"system_check"}' > ~/VoxeraOS/notes/queue/job-1.json
voxera daemon --once
```
The daemon watches `~/VoxeraOS/notes/queue/` for JSON jobs (`mission_id` or `goal`) and
moves completed jobs to `done/` and failures to `failed/`.

### 2e) Run end-to-end smoke checks
```bash
make e2e
# optional live mission:
E2E_RUN_LIVE=1 make e2e
```
The smoke script checks optional OS tools (`wmctrl`, `xdg-open`, clipboard utilities, `pactl`)
and prints install hints when missing.

### 3) Start the panel (optional)
```bash
voxera panel
# open http://127.0.0.1:8844
```

## How the “OS” is structured
Voxera is designed as three layers:
1. **Substrate OS** (Ubuntu/Fedora/etc.) — drivers, updates, filesystem, networking
2. **AI Control Plane** — intent routing, planning, memory, tool runner, policy enforcement
3. **Experience Layer** — voice shell + minimal confirmation panel (and later full GUI/CLI modes)

See `docs/ARCHITECTURE.md`, `docs/BOOTSTRAP.md`, and `docs/CODEX_MEMORY.md`.

For Ubuntu validation, follow `docs/UBUNTU_TESTING.md` for a full machine test checklist.

## Safety model (MVP)
- No silent risky changes (network/install/credentials)
- Everything is audited (what/why/how to undo)
- Skills declare permissions; policies decide “allow/ask/deny”

## Roadmap
- Voice stack (wake word + STT/TTS)
- Container sandbox runner (Podman)
- First-boot “installer-by-conversation” flow
- Immutable base image (Silverblue-style) for atomic upgrades + rollback

---
**This is scaffolding**: it’s meant to get you to a working GitHub first commit and a fast iteration loop.

`files.write_text` now supports `mode=overwrite|append` for note updates, and mission runs append summaries to `~/VoxeraOS/notes/mission-log.md` (redacted when `privacy.redact_logs` is enabled).

