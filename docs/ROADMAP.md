# Roadmap

This roadmap intentionally starts with the documentation truth update as the next real PR, then sequences the next three milestone chapters.

- **PR 162 (this PR):** `docs(roadmap)` milestone framing + PR-number realignment.
- **v0.1.8-Alpha:** Vera Control Layer.
- **v0.1.9-Alpha:** Governed Capability Expansion.
- **v0.2.0-Alpha:** First Platform Milestone.

The plan aligns to the project North Star: **Vera is the conversational intelligence layer; VoxeraOS is the trust, policy, execution, approvals, evidence, and operator control layer**. Releases are evaluated against the same architecture taste: **observable, deterministic where possible, auditable, testable, operator-friendly, and safe-by-default**.

---

## v0.1.8-Alpha — Vera Control Layer

**Milestone meaning**

Vera becomes a stable, trustworthy conversational control layer for VoxeraOS.

This release hardens the current operating loop so it reliably feels strong end-to-end:

1. understand intent
2. draft actionable work
3. revise collaboratively
4. submit through governed rails
5. review against evidence
6. prepare the next step

This is where Vera should stop feeling like a thin chat surface and start feeling like a dependable control interface that remains honest about trust boundaries.

### Planned PRs

- **PR 162** — `docs(roadmap)`: expand and update roadmap for v0.1.8, v0.1.9, and v0.2.0
- **PR 163** — `fix(vera)`: finalize authoritative preview synchronization
- **PR 164** — `feat(vera)`: stronger conversational draft revision engine
- **PR 165** — `feat(vera)`: evidence-aware follow-up draft generation
- **PR 166** — `feat(vera)`: better natural-language task shaping into governed actions
- **PR 167** — `feat(vera/ui)`: chat-first usability pass
- **PR 168** — `fix(vera)`: reduce misleading or overly internal fallback messages
- **PR 169** — `feat(vera)`: draft confidence and clarification behavior
- **PR 170** — `feat(vera/review)`: richer result explanation from canonical evidence
- **PR 171** — `feat(vera)`: better result-to-next-step conversational transitions
- **PR 172** — `feat(vera)`: safer and clearer preview confirmation / submit intent handling
- **PR 173** — `feat(vera/ui)`: stronger preview pane stability and interaction polish
- **PR 174** — `feat(vera)`: better multi-turn intent continuity and context carry-through
- **PR 175** — `feat(vera)`: clearer conversational shaping for file / open / review flows
- **PR 176** — `docs/demo`: sharpen Vera demo and operator story for 0.1.8
- **PR 178** — `fix(vera)`: hidden Voxera-aware preview compiler + authoritative preview-pane routing across draftable intents
- **PR 177** — `release(0.1.8)`: Vera Control Layer milestone packaging

---

## v0.1.9-Alpha — Governed Capability Expansion

**Milestone meaning**

VoxeraOS becomes a broader governed capability platform, not just a narrow queue/control demo.

This release expands what the system can do safely and usefully while preserving trust boundaries: capability-gated execution, policy-evaluated requests, deterministic routing where possible, and evidence-backed outcomes.

**Target size:** 12 to 16 PRs.

### Planned PRs

- **PR 178** — `feat(system)`: governed read-only system inspection lane
- **PR 179** — `feat(system)`: governed installed-software and capability inventory lane
- **PR 180** — `feat(system)`: governed service/runtime inspection lane
- **PR 181** — `feat(web)`: governed read-only URL retrieval lane
- **PR 182** — `feat(web)`: web retrieval result shaping and artifact evidence
- **PR 183** — `feat(web)`: better Vera summarization and review over web retrieval results
- **PR 184** — `feat(files)`: richer governed file operations
- **PR 185** — `feat(files)`: clearer overwrite/append/read semantics and safer file evidence
- **PR 186** — `feat(capabilities)`: internal capability registry and route metadata upgrade
- **PR 187** — `feat(capabilities)`: stronger route explainability and operator-facing capability summaries
- **PR 188** — `feat(vera)`: better routing into expanded governed capability families
- **PR 189** — `feat(panel)`: operator console comprehension pass
- **PR 190** — `feat(observability)`: stronger artifacts and execution evidence summaries
- **PR 191** — `feat(observability)`: better related-job / lineage / evidence navigation
- **PR 192** — `docs/demo`: governed capability expansion packaging for 0.1.9
- **PR 193** — `release(0.1.9)`: Governed Capability Expansion milestone packaging

---

## v0.2.0-Alpha — First Platform Milestone

**Milestone meaning**

This is the first release where Vera + VoxeraOS should feel like a coherent AI operating system platform, not just an advanced prototype.

The milestone combines runtime coherence, planning maturity, operator-console quality, setup polish, and replayable evidence packaging so the platform is deployable under real operational pressure.

**Target size:** 12 to 16 PRs.

### Planned PRs

- **PR 194** — `feat(runtime)`: shared operational session context between Vera and VoxeraOS
- **PR 195** — `feat(runtime)`: richer environment awareness for planning and review
- **PR 196** — `feat(runtime)`: better session-scoped operational memory of recent jobs and outcomes
- **PR 197** — `feat(vera)`: stable end-to-end conversational operating loop
- **PR 198** — `feat(vera)`: task-to-capability planning maturity pass
- **PR 199** — `feat(vera)`: stronger multi-step conversational planning without losing trust boundaries
- **PR 200** — `feat(panel)`: first serious operator-console polish milestone
- **PR 201** — `feat(panel)`: better approvals UX and runtime state comprehension
- **PR 202** — `feat(setup)`: polished first-run and runtime bring-up experience
- **PR 203** — `feat(demo)`: polished demo mode and product storytelling flow
- **PR 204** — `feat(ops)`: stronger stack management and operational ergonomics
- **PR 205** — `feat(artifacts)`: stronger replayable evidence and result packaging
- **PR 206** — `feat(voice-foundation)`: prepare the architecture for voice-first operation
- **PR 207** — `docs/architecture`: major platform-story consolidation
- **PR 208** — `docs/product`: clearer public explanation of Vera + VoxeraOS as a platform
- **PR 209** — `release(0.2.0)`: first platform milestone packaging

---

## Roadmap guardrails

Across all milestones, roadmap execution keeps the same non-negotiables:

- no silent side effects
- no mutation paths outside governed capability rails
- no policy bypass by prompt tricks or metadata drift
- no unaudited execution
- fail closed when uncertain
- every important action produces inspectable evidence
