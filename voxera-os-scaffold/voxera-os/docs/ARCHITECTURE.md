# Architecture

Voxera OS is an AI control plane that sits *above* a real OS substrate.

## Layers
1) Substrate OS
- Linux (Ubuntu for dev, later immutable base)
- drivers, networking, package manager, audio stack

2) Voxera Control Plane
- Intent router (voice/GUI/CLI)
- Planner (multi-step tasks)
- Memory (preferences/workflows)
- Skill registry (versioned tools)
- Tool runner (sandboxed execution)
- Policy/permissions engine (capabilities)

3) Experience Layer
- Voice shell (wake + STT/TTS) — planned
- Minimal Panel (confirmations + audit)
- CLI for power users

## Key principles
- Capability-based permissions
- No silent risky actions
- Audit + replay: “what / why / how to undo”
- Rollback-first for config and operations

## Data flow (MVP)
User (voice/CLI) -> Router -> Planner -> Skill selection -> Policy gate -> Runner -> Audit -> Response

See also: BOOTSTRAP.md, SECURITY.md, ROADMAP.md.
