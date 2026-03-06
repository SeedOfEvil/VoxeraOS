<div align="center">

```
 __   ___                     ___  ____
 \ \ / / |__  _  _  ___  _ _ / _ \/ ___|
  \ V /| '_ \| \/ |/ -_)| '_| (_) \___ \
   \_/ |_.__/ \__/ \___||_|  \___/|____/
```

# VoxeraOS

**An AI control plane for your Linux machine — powered by Vera, your on-device AI persona.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![Alpha](https://img.shields.io/badge/Status-Alpha%20v0.1.6-orange?style=flat-square)](docs/ROADMAP_0.1.6.md)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![FastAPI](https://img.shields.io/badge/Panel-FastAPI%20%2B%20Uvicorn-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)

*Tell Vera what you want. She plans it, gates it through policy, and executes it — with a full audit trail.*

</div>

---

## What is VoxeraOS?

VoxeraOS is a **voice-first AI control plane** that sits on top of standard Linux (Ubuntu). Instead of clicking through menus or writing scripts, you describe your goal in plain English. VoxeraOS's core AI persona — **Vera** — routes that intent through a planning → policy → execution → audit pipeline and gets it done.

> Think of it as an AI that *actually controls your OS* — not just suggests commands.

**Key design principles:**
- **Intent-driven** — Describe goals, not steps. Vera figures out the plan.
- **Policy-gated** — Every action is approved by a capability-based policy engine before execution.
- **Auditable** — Every skill call is logged to an append-only JSONL audit trail.
- **Recoverable** — Single-writer lock, graceful shutdown, and startup recovery built in.
- **Sandboxed** — Untrusted code runs in rootless Podman containers with no network by default.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXPERIENCE LAYER                         │
│           Web Panel  ·  CLI (voxera)  ·  Voice (planned)        │
└────────────────────────────┬────────────────────────────────────┘
                             │ intents / jobs
┌────────────────────────────▼────────────────────────────────────┐
│                      AI CONTROL PLANE                           │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │ Intent Router│──▶│Mission Planner│──▶│  Policy Engine   │    │
│  └──────────────┘   └──────────────┘   └────────┬─────────┘    │
│                                                  │              │
│  ┌──────────────┐   ┌──────────────┐   ┌────────▼─────────┐    │
│  │  Audit Log   │◀──│ Skill Runner │◀──│ Approval Workflow │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │ Queue Daemon │   │ Health Monitor│   │  Skill Registry  │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
└────────────────────────────┬────────────────────────────────────┘
                             │ syscalls / APIs
┌────────────────────────────▼────────────────────────────────────┐
│                       SUBSTRATE (Linux)                         │
│        Filesystem  ·  Audio  ·  Networking  ·  systemd          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Feature Highlights

### 🧠 Cloud-Backed Mission Planning
Vera uses pluggable AI brain providers to turn your goals into multi-step execution plans. A **deterministic fast-path** handles simple goals locally (no API call needed).

- Pluggable providers: **Google Gemini**, **OpenAI-compatible** (OpenRouter, Ollama, local models)
- Brain fallback chain: `primary → fast → reasoning → fallback`
- Classified failure reasons: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`
- **Prompt injection hardened**: goal strings sanitized, 2,000-char cap enforced, structural delimiters in planner prompt

### ⚙️ Reliable Queue Daemon
Drop a JSON job file in an inbox folder and walk away. The queue daemon handles everything.

```
inbox/*.json  →  pending  →  done
                    └──────→  pending/approvals/*.approval.json  (approval gated)
                    └──────→  failed / canceled
```

- Atomic job intake with single-writer lock
- Approval workflow: jobs pause and surface to the web panel for human approval
- Health snapshot (`health.json`) persists lock state, backoff, auth counters, shutdown outcomes
- Startup recovery: orphan state files auto-quarantined to `recovery/startup-<ts>/`
- Graceful SIGTERM shutdown

### 🔐 Capability-Based Policy Engine
Every skill declares the capabilities it needs. The policy engine maps those to `allow / ask / deny` per your config — before any execution happens.

| Capability | Examples |
|---|---|
| `files` | Read/write filesystem paths |
| `network` | Fetch URLs, call APIs |
| `apps` | Open applications |
| `settings` | Change system settings |
| `install` | Install packages |

### 🛠 Built-In Skills (11)
Skills are the atomic units of execution — versioned, sandboxed, and audited.

| Skill | What it does |
|---|---|
| `files.read_text` | Read file contents |
| `files.write_text` | Write or create files |
| `clipboard.copy` | Copy text to clipboard |
| `clipboard.paste` | Read clipboard contents |
| `system.open_app` | Launch applications |
| `system.open_url` | Open URLs in browser |
| `system.set_volume` | Set system audio volume |
| `system.status` | Report system state |
| `system.terminal_run_once` | Run a terminal command (once) |
| `sandbox.exec` | Execute code in rootless Podman |
| `window_list` | List open windows |

### 🌐 Web Panel
A full FastAPI-powered web panel for visibility and control — no CLI required.

- **Dashboard**: Queue status, daemon health widget, job counts
- **Job Manager**: View, approve, deny, retry, cancel, delete jobs
- **Advisor**: Advisory-only "Ask Vera" lane with multi-turn thread continuity
- **Hygiene**: Dry-run prune/reconcile triggers with health.json results
- **Recovery Inspector**: Browse quarantine sessions, download ZIP for triage
- **Auth**: Basic auth with per-IP rate limiting (10 failures/60s → HTTP 429 + `Retry-After`)

### 📋 Full Audit Trail
Every skill execution is appended to a JSONL audit log:

```json
{
  "ts": "2025-03-01T14:23:01Z",
  "job_id": "abc123",
  "skill": "files.write_text",
  "args": {"path": "~/notes/today.md", "content": "[redacted]"},
  "result": "ok",
  "policy_reason": "allow"
}
```

---

## Quick Start

### Prerequisites
- Ubuntu 22.04+ (or compatible Linux)
- Python 3.10+
- Podman (for `sandbox.exec` skill)
- A brain provider API key (Gemini or OpenRouter)

### Install

```bash
# Clone and set up venv
git clone https://github.com/SeedOfEvil/VoxeraOS.git
cd VoxeraOS
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

### First Run

```bash
# Interactive setup wizard — configures providers, stores secrets securely
voxera setup

# Verify your setup
voxera doctor

# Check system status
voxera status
```

### Start the Daemon

```bash
# One-time queue initialization
voxera queue init

# Enable as a systemd user service (auto-starts on login)
systemctl --user enable --now voxera-daemon.service

# Or run once for testing
voxera daemon --once
```

### Submit Your First Job

```bash
# Via CLI
voxera inbox add "Write a daily check-in note with today's top priorities"

# Or drop a JSON file directly
echo '{"goal": "open Spotify and set volume to 60%"}' \
  > ~/VoxeraOS/notes/queue/inbox/job-1.json
```

### Manage Jobs

```bash
# See everything at a glance
voxera queue status

# List pending approvals
voxera queue approvals list

# Approve a job
voxera queue approvals approve <job_id>

# Run the web panel
voxera panel
# → http://localhost:7900
```

---

## CLI Reference

```
voxera setup               First-run wizard (providers, secrets, config)
voxera doctor              Diagnose brain provider health
voxera doctor --self-test  Full end-to-end self-test

voxera status              System and daemon status
voxera skills list         List all available skills
voxera run <skill> [args]  Execute a skill directly

voxera inbox add "<goal>"  Submit a natural-language job
voxera queue status        Full queue overview (jobs, approvals, health)
voxera queue health        Operator health snapshot
voxera queue prune         Remove old completed jobs (dry-run by default)
voxera queue reconcile     Detect orphans/duplicates (report-only default)

voxera missions plan "<goal>"   Plan a mission without executing
voxera config show              Show current config (secrets redacted)
voxera config snapshot          Export redacted config snapshot

voxera panel               Start the web UI
```

---

## Configuration

| File | Purpose |
|---|---|
| `~/.config/voxera/config.yml` | Brain providers, API keys, policy settings |
| `~/.config/voxera/config.json` | Runtime ops (panel host/port, queue paths) |
| `~/.config/voxera/env` | Secret fallback (0600, used when keyring unavailable) |
| `~/VoxeraOS/notes/queue/` | Queue root: inbox, pending, done, failed, artifacts |
| `~/.voxera/data/audit/YYYY-MM-DD.jsonl` | Daily audit logs |

Secrets are stored via **system keyring** with a `0600` file fallback. They are always **redacted** in `config show` and `config snapshot` output.

---

## Development

```bash
make dev           # Set up venv + dev tools
make check         # Full validation: fmt + lint + types + tests
make fmt-check     # Format check (Ruff)
make lint          # Linter (Ruff)
make type          # Type check (Mypy)
make test          # Run pytest (~60 test files)

make services-install  # Install systemd units
make daemon-restart    # Restart daemon service
make update            # Pull latest + reinstall
```

**Tech Stack:**

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| CLI | Typer + Rich |
| Data models | Pydantic v2 |
| Web panel | FastAPI + Uvicorn + Jinja2 |
| HTTP client | httpx (async) |
| AI backends | Google Gemini, OpenAI-compatible (OpenRouter, Ollama) |
| Sandbox | Podman (rootless, `--network=none` default) |
| Service management | systemd user units |
| Secrets | keyring + 0600 fallback |
| Linting / formatting | Ruff |
| Type checking | Mypy |
| Tests | pytest + pytest-asyncio |

---

## Project Layout

```
VoxeraOS/
├── src/voxera/
│   ├── cli.py                    # CLI entry point
│   ├── brain/                    # AI provider adapters (Gemini, OpenAI-compat)
│   ├── core/
│   │   ├── queue_daemon.py       # Daemon orchestration root
│   │   ├── mission_planner.py    # LLM planning + fallback chain
│   │   ├── missions.py           # Mission templates + runner
│   │   ├── router.py             # Intent routing
│   │   ├── queue_approvals.py    # Approval workflow
│   │   ├── queue_recovery.py     # Startup recovery + quarantine
│   │   └── queue_reconcile.py    # Orphan detection + auto-fix
│   ├── skills/                   # Skill registry, runner, policy gating
│   ├── panel/                    # FastAPI web UI + templates
│   ├── policy.py                 # Capability → allow/ask/deny
│   └── audit.py                  # JSONL audit logger
├── src/voxera_builtin_skills/    # 11 built-in skill implementations
├── skills/                       # Skill manifests (YAML)
├── missions/                     # Mission template files
├── tests/                        # pytest suite (~60 files)
├── docs/                         # Architecture, security, ops, roadmap
├── deploy/systemd/user/          # systemd unit files
└── Makefile                      # 30+ build targets
```

---

## Roadmap

| Version | Theme | Status |
|---|---|---|
| v0.1.4 | Stability + UX baseline (typed setup, cloud planner, queue daemon, web panel) | ✅ Shipped |
| v0.1.5 | Artifacts hygiene + prune/reconcile tooling | ✅ Shipped |
| v0.1.6 | Security hardening + ops visibility (prompt injection defense, auth lockout, health widget) | ✅ Shipped |
| v0.2 | Voice input, richer skill ecosystem, local model improvements | 🚧 Planned |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full planned scope.

---

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Three-layer model, data flow, component interaction, module map |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, capability-based permissions, prompt hardening, sandbox |
| [`docs/ops.md`](docs/ops.md) | Operator runbook: incident response, health checks, recovery procedures |
| [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md) | First-run installation guide |
| [`docs/LOCAL_MODELS.md`](docs/LOCAL_MODELS.md) | Local model setup (Ollama) |
| [`docs/UBUNTU_TESTING.md`](docs/UBUNTU_TESTING.md) | Ubuntu-specific testing notes |

---

## Names & Terminology

| Name | Role |
|---|---|
| **VoxeraOS** | The project / OS experience layer |
| **Vera** | The core AI persona |
| **voxera** | The CLI command |
| **Hey Voxera** | Planned voice wake word (v0.2+) |

---

<div align="center">

*VoxeraOS is alpha software. APIs and config formats may change between versions.*

</div>
