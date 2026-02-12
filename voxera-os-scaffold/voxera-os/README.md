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
- ✅ Provider abstraction layer + adapters (OpenAI-compatible works immediately with local servers like Ollama)
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

### 2) Try basic commands
```bash
voxera status
voxera skills list
voxera run system.status
```

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
