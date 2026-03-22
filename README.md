# VoxeraOS

**A queue-driven AI control plane for Linux — where Vera thinks freely and VoxeraOS holds the line.**

[![Alpha](https://img.shields.io/badge/status-alpha%20v0.1.8-orange)]()
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)]()

---

## What is this?

VoxeraOS is a local-first AI control plane that turns natural-language intent into auditable, policy-gated queue jobs on Linux.

It provides three operator surfaces — a CLI (`voxera`), a web panel, and a conversational assistant called **Vera** — all backed by a queue daemon that enforces lifecycle visibility, approval gates, and evidence-backed execution.

**Vera** is the conversational intelligence layer. She understands intent, drafts actionable work, and guides you through it — but she does not get to change real systems on her own.

**VoxeraOS** is the trust layer. Every real-world side effect — file mutations, service actions, privileged operations — must go through the VoxeraOS queue, where it is policy-evaluated, approval-gated when needed, and evidence-tracked.

The core idea: let AI stay free in reasoning space, keep execution controlled in capability space.

## Project status

VoxeraOS is an **open-source alpha (v0.1.8)** — a working demo product, not production software.

This is a **one-person evenings-and-weekends project**. The architecture is real, the end-to-end flows work, and the framework is honest about its boundaries. But many things will change, some current decisions are transitional, and some implementations still feel rough around the edges.

**What is working today:**
- Queue-driven mission execution with deterministic lifecycle buckets
- Approval workflows (human-in-the-loop gates for policy-sensitive actions)
- Vera conversational surface with governed preview/draft/submit flows
- Operator panel with job dashboards, approvals, hygiene, and recovery tools
- Advisory assistant lane with provider fallback
- Intent routing with fail-closed guardrails
- Security hardening: red-team regression suite, traversal defenses, prompt boundary controls
- Health monitoring, doctor diagnostics, incident bundle exports
- Governed file operations, web investigation, weather lookups, code/writing drafts

**What is still early or evolving:**
- Voice-first interaction (the long-term North Star, not yet built)
- Mission catalog breadth (six built-in templates; more planned)
- Provider compatibility beyond OpenRouter (see below)
- Panel UX polish and mobile responsiveness
- Multi-step orchestration maturity
- Setup and onboarding smoothness

This is an honest alpha. The architecture is solid, the demo works end-to-end, and it is worth exploring — but do not deploy this in production.

## Provider support

**OpenRouter is the only officially tested and fully built provider path today.**

- **Minimum supported requirement:** Gemini 3 Flash (`google/gemini-3-flash-preview`) via OpenRouter
- The setup wizard configures four brain slots (`primary`, `fast`, `reasoning`, `fallback`) with curated OpenRouter model defaults
- Other OpenRouter-available models may work and are welcome to be tested by the community
- Ollama and other OpenAI-compatible endpoints are architecturally supported but have not been extensively validated

If you try other models or providers, please report back what works and what doesn't — community feedback is explicitly welcome via [GitHub Issues](https://github.com/SeedOfEvil/VoxeraOS/issues).

## Quick start

```bash
# Clone and set up
git clone https://github.com/SeedOfEvil/VoxeraOS.git
cd VoxeraOS
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

Initialize local config and queue directories (guided wizard):

```bash
voxera setup
voxera queue init
```

`voxera setup` walks you through brain slot configuration using a curated OpenRouter model catalog. After setup completes, it can start the service stack and open VoxeraOS and Vera for you.

Run the core runtime stack locally:

```bash
voxera daemon
voxera panel --host 127.0.0.1 --port 8787
make vera
```

Vera defaults to `127.0.0.1:8790`.

For systemd user-service management:

```bash
make services-install
make services-status
make vera-status
make vera-logs
make vera-restart
```

### Secrets CLI (provider keys)

```bash
voxera secrets set OPENROUTER_API_KEY
voxera secrets set BRAVE_API_KEY      # optional, for web investigation
voxera secrets get BRAVE_API_KEY --exists-only
```

## Common workflows

### Queue + daemon

```bash
voxera inbox add "Run a quick system check"
voxera queue status
voxera daemon --once
```

### Approvals

```bash
voxera queue approvals list
voxera queue approvals approve <job_ref>
voxera queue approvals deny <job_ref>
```

### Health + diagnostics

```bash
voxera queue health
voxera doctor --quick
voxera doctor --self-test
```

### Hygiene + recovery

```bash
voxera queue reconcile --json
voxera queue prune --max-age-days 14
voxera artifacts prune --max-age-days 30
```

### Incident bundles

```bash
voxera ops bundle system
voxera ops bundle job <job_ref>
```

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────┐
│  Experience Layer                                       │
│  Vera (chat) · Web Panel · CLI (voxera)                 │
├─────────────────────────────────────────────────────────┤
│  AI Control Plane                                       │
│  Intent router · Mission planner · Queue daemon         │
│  Skill registry · Policy engine · Approval workflow     │
│  Audit log · Health monitor                             │
├─────────────────────────────────────────────────────────┤
│  Substrate OS                                           │
│  Linux (Ubuntu) · Filesystem · Systemd user services    │
└─────────────────────────────────────────────────────────┘
```

**Vera** operates in the Experience Layer — she can reason, explore, and draft freely.

**VoxeraOS** operates in the Control Plane — every real-world side effect is capability-gated, policy-evaluated, approval-gated when needed, and evidence-backed.

The queue is the system boundary. Jobs flow through deterministic lifecycle buckets (`inbox/` → `pending/` → `done/` / `failed/` / `canceled/`) with sidecars and artifacts for debugging and audit.

For deeper architectural detail, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository structure

```
src/voxera/core/       — queue daemon, mission planner, control-plane internals
src/voxera/panel/      — FastAPI operator panel and route modules
src/voxera/cli.py      — CLI entrypoint (Typer composition root)
src/voxera/cli_*.py    — focused CLI command modules
src/voxera/skills/     — skill registry, runner, path boundaries
src/voxera/vera_web/   — Vera conversational web app
src/voxera/brain/      — LLM adapter layer (OpenRouter, Gemini, OpenAI-compat)
docs/                  — architecture, operations, security, roadmap
missions/              — built-in mission templates
tests/                 — unit, contract, golden, and red-team security tests
deploy/systemd/user/   — user service units for daemon, panel, and Vera
```

## Roadmap

VoxeraOS is organized around three near-term milestone themes:

- **v0.1.8 (current)** — Vera Control Layer: make Vera a stable, trustworthy conversational control interface for VoxeraOS
- **v0.1.9** — Governed Capability Expansion: broaden what the system can do safely (system inspection, web retrieval, richer file operations, capability registry)
- **v0.2.0** — First Platform Milestone: make Vera + VoxeraOS feel like a coherent AI operating platform (session context, planning maturity, operator console polish, voice foundation)

The long-term North Star is a **voice-first AI operating system** — an AI you can talk to that feels alive but behaves like infrastructure. Vera is the intelligence; VoxeraOS is the trust layer. See [docs/NORTH_STAR.md](docs/NORTH_STAR.md) for the full vision.

Detailed roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture map and module boundaries |
| [docs/ops.md](docs/ops.md) | Day-2 operations and service workflows |
| [docs/SECURITY.md](docs/SECURITY.md) | Security posture, threat model, and hardening notes |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestone roadmap and release tracking |
| [docs/NORTH_STAR.md](docs/NORTH_STAR.md) | Product direction and non-negotiables |
| [docs/CODEX_MEMORY.md](docs/CODEX_MEMORY.md) | Implementation history / PR changelog |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Security reporting guidance |
| [CHANGELOG.md](CHANGELOG.md) | Release changelog |

## Contributing

VoxeraOS welcomes contributions, feedback, and experimentation. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidance.

Before opening a PR, run the canonical validation:

```bash
make merge-readiness-check
```

For release-grade confidence:

```bash
make full-validation-check
```

## Development quick reference

```bash
make fmt              # format code
make lint             # lint check
make type             # mypy type check
make test             # run tests
make golden-check     # validate CLI contract baselines
make security-check   # run red-team regression suite
make validation-check # standard quick gate
make merge-readiness-check  # CI-required merge gate
make full-validation-check  # full suite including E2E
make update-mypy-baseline   # update typing baseline (intentional only)
```

CI-required merge gate: `make merge-readiness-check` (`merge-readiness / merge-readiness`).

Note: preserve the existing merge gate semantics documented as `merge-readiness / merge-readiness` when touching release process docs.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## About this project

VoxeraOS is built and maintained by a single developer in evenings and weekends. It is open source because the architecture and ideas are worth sharing, even in alpha form.

If you find VoxeraOS interesting, have questions, want to test it with different models, or want to contribute — [open an issue](https://github.com/SeedOfEvil/VoxeraOS/issues) or start a discussion. Feedback of all kinds is welcome.
