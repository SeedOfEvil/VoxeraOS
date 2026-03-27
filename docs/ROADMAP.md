# Roadmap

VoxeraOS development is organized around milestone themes that build toward the long-term North Star: a voice-first AI operating system where Vera is the conversational intelligence and VoxeraOS is the trust, policy, execution, and evidence layer.

Releases are evaluated against the same architecture taste: **observable, deterministic where possible, auditable, testable, operator-friendly, and safe-by-default**.

This roadmap communicates broad direction and major milestones. Specific PRs and implementation details are tracked in working development notes and are likely to shift as the project evolves.

---

## v0.1.8-Alpha — Vera Control Layer (release tag; shipped)

**Theme:** Make Vera a stable, trustworthy conversational control interface for VoxeraOS.

This release hardens the operating loop so it reliably works end-to-end:

1. Understand intent
2. Draft actionable work
3. Revise collaboratively
4. Submit through governed rails
5. Review against evidence
6. Prepare the next step

Key areas:
- Preview synchronization and authoritative draft behavior
- Conversational draft revision and follow-up generation
- Natural-language task shaping into governed actions
- Chat-first usability improvements
- Result explanation from canonical evidence
- Preview confirmation and submit intent handling
- Multi-turn intent continuity

---

## v0.1.9-Alpha — Governed Capability Expansion (theme largely shipped on current branch)

**Theme:** Broaden what VoxeraOS can do safely and usefully while preserving trust boundaries.

Key areas (current implementation state):
- ✅ Governed read-only system inspection lanes are shipped (`system_inspect`, `system_diagnostics`, and related skills)
- ✅ Governed read-only URL retrieval/investigation lane is shipped for Vera (`Brave`-backed, read-only)
- ✅ Richer governed file operations are shipped (queue helper CLI + bounded file skill family)
- ✅ Capability semantics contract + snapshot surfaces are shipped and used by planning/review/policy surfaces
- ✅ Artifact/result shaping for operator evidence is shipped (execution/result/envelope + review/evidence summaries)
- 🔄 Ongoing: operator-console comprehension and UX polish continues toward v0.2

---

## v0.2.0-Alpha — First Platform Milestone (next)

**Theme:** Make Vera + VoxeraOS feel like a coherent AI operating system platform, not just a prototype.

Key areas:
- Shared operational session context between Vera and VoxeraOS
- Richer environment awareness for planning and review
- Stable end-to-end conversational operating loop
- Task-to-capability planning maturity
- Operator console polish milestone
- Polished first-run and demo experience
- Stronger replayable evidence and result packaging
- Voice foundation progression (current bounded seam exists; full voice UX remains future work)

---

## Longer-term direction

Beyond v0.2, the broad direction includes:

- **Voice-first interaction** — the long-term North Star; making spoken interaction the primary surface
- **Deeper orchestration** — multi-step workflows, richer parent/child coordination, workflow composition
- **Broader provider support** — validating and hardening additional LLM providers beyond OpenRouter
- **Mission catalog maturity** — richer built-in and community-contributed mission templates
- **External integration lanes** — governed interaction with external services and APIs
- **Sandbox and isolation improvements** — tighter Podman profiles, seccomp, AppArmor
- **Signed skills and integrity verification** — tamper-resistant skill entrypoints

---

## Roadmap guardrails

Across all milestones, roadmap execution keeps the same non-negotiables:

- No silent side effects
- No mutation paths outside governed capability rails
- No policy bypass by prompt tricks or metadata drift
- No unaudited execution
- Fail closed when uncertain
- Every important action produces inspectable evidence
