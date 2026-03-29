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
- Governed file operations (read + mutating queue helpers), web investigation, weather lookups, code/writing drafts
- Capability semantics + manifest-derived policy/approval interpretation surfaces
- Built-in mission catalog (9 in-code templates) plus file-based mission loading from `missions/` and `~/.config/voxera/missions`

**What is still early or evolving:**
- Voice-first interaction UX (full duplex voice loop is not yet built; only a bounded voice foundation seam exists today)
- Mission/catalog breadth is growing (current in-code catalog ships 9 built-in templates; broader community catalog remains future work)
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
cd ~
git clone https://github.com/SeedOfEvil/VoxeraOS.git
cd ~/VoxeraOS
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```
Current alpha note: the officially supported workspace path is ~/VoxeraOS. Some notes, queue, and workspace flows still assume that location for now.

Initialize local config and queue directories (guided wizard):

```bash
voxera setup
voxera queue init
```

`voxera setup` walks you through brain slot configuration using a curated OpenRouter model catalog. After setup completes, it can start the service stack and open VoxeraOS and Vera for you.

Run the core runtime stack locally:

```bash
voxera daemon
voxera panel --host 127.0.0.1 --port 8844
make vera
```

Panel runtime default is `127.0.0.1:8844` (the `make panel` dev shortcut intentionally uses `127.0.0.1:8787`).
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

### Governed filesystem queue helpers

```bash
# Read/discovery
voxera queue files find --root-path ~/VoxeraOS/notes/runtime-validation --glob "*.md"
voxera queue files grep --root-path ~/VoxeraOS/notes --pattern "queue"
voxera queue files tree --root-path ~/VoxeraOS/notes/runtime-validation --max-depth 3

# Mutations still go through queue truth + artifacts
voxera queue files copy --source-path ~/VoxeraOS/notes/runtime-validation/a.txt --destination-path ~/VoxeraOS/notes/runtime-validation/b.txt
voxera queue files move --source-path ~/VoxeraOS/notes/runtime-validation/b.txt --destination-path ~/VoxeraOS/notes/runtime-validation/c.txt
voxera queue files rename --path ~/VoxeraOS/notes/runtime-validation/c.txt --new-name renamed.txt

# Execute queued jobs
voxera daemon --once
voxera queue status
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

Capability semantics are normalized centrally in `src/voxera/core/capability_semantics.py` (effect class, intent class, resource boundaries, and policy mapping) so manifests, missions, approvals, and operator summaries derive from one contract.

The queue is the system boundary. Jobs flow through deterministic lifecycle buckets (`inbox/` → `pending/` → `done/` / `failed/` / `canceled/`) with sidecars and artifacts for debugging and audit.

For deeper architectural detail, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
For the bounded hotspot responsibility map and PR-by-PR extraction sequence for `vera_web/app.py`, `panel/app.py`, and `cli_queue.py`, see [docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md](docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md).

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

### Refactor status: current module ownership map

Recent refactors intentionally reduced the amount of Vera, queue, and panel behavior concentrated in single hotspot files. The current ownership boundaries are:

- **Vera conversational orchestration**
  - `src/voxera/vera/service.py` — top-level Vera reply orchestration, provider selection, linked-job completion delivery, and compatibility delegation
  - `src/voxera/vera/session_store.py` — bounded session persistence and active-preview state
  - `src/voxera/vera/preview_drafting.py` — deterministic preview drafting and save-by-reference preview shaping
  - `src/voxera/vera/draft_revision.py` — active preview rename/path/content follow-up interpretation
  - `src/voxera/vera/preview_submission.py` — active-preview submit detection, payload normalization, and queue handoff acknowledgement
  - `src/voxera/vera/investigation_flow.py` — explicit read-only web investigation orchestration
  - `src/voxera/vera/investigation_derivations.py` — compare/summarize/expand follow-up handling and derived markdown/save previews
  - `src/voxera/vera/weather_flow.py` — quick live-weather routing and follow-up continuity
  - `src/voxera/vera/saveable_artifacts.py` — meaningful recent assistant-content selection for governed save flows
  - `src/voxera/vera/handoff.py` — intentionally thin compatibility façade across the extracted handoff-facing seams
  - Integrity invariant: visible preview state is authoritative pre-submit and is the exact source for queued payload serialization; ambiguous preview state fails closed; accepted naming mutations explicitly confirm the new destination path; linked completion surfacing prioritizes the latest linked submit in-session; clear single-turn generate+save requests can bind same-turn authored content without requiring prior artifacts; linked-completion status text and draft-management/explanatory wrapper narration are not eligible default note-body content.

- **Queue orchestration**
  - `src/voxera/core/queue_daemon.py` — daemon lifecycle, lock handling, directory contract, and composition root
  - `src/voxera/core/queue_execution.py` — payload normalization, mission construction, planning handoff, and execution-state transitions
  - `src/voxera/core/queue_approvals.py` — approval prompts, grants, pending artifacts, and approve/deny resolution
  - `src/voxera/core/queue_recovery.py` — startup recovery, shutdown handling, and quarantine/report shaping
  - `src/voxera/core/queue_contracts.py` / `queue_result_consumers.py` / `queue_state.py` / `queue_paths.py` — queue object shaping, evidence/result normalization, lifecycle state sidecars, and deterministic movement/path helpers

- **Panel composition**
  - `src/voxera/panel/app.py` — FastAPI wiring root, shared auth/security helpers, health/job view helpers, and route registration
  - `src/voxera/panel/routes_*.py` — route-family ownership split across home, jobs, queue control, missions, hygiene, recovery, bundle, and assistant surfaces
  - `src/voxera/panel/assistant.py` — assistant-thread persistence helpers used by the operator advisory lane

- **Config and path layers**
  - `src/voxera/config.py` — runtime/operator config loading (`config.json`), app/provider config loading (`config.yml`), config snapshots, and fingerprinting
  - `src/voxera/paths.py` — XDG config/data path helpers plus default queue-root resolution

When extending one of these areas, prefer adding code to the dedicated ownership module first instead of re-growing the legacy compatibility façades or composition roots.

## Roadmap

VoxeraOS is organized around three near-term milestone themes:

- **v0.1.8 (current release tag)** — Vera Control Layer foundations are shipped on this branch
- **v0.1.9 theme (largely landed on current branch)** — governed capability expansion is already present (system inspection/diagnostics, read-only investigation lanes, richer file helpers, capability semantics contracts)
- **v0.2.0 (next milestone framing)** — platform polish and integration depth (session/planning maturity, operator-console refinement, and continued voice-foundation-to-UX progression)

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
