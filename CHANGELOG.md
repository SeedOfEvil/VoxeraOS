# Changelog

All notable changes to VoxeraOS will be documented in this file.

VoxeraOS is an open-source alpha project. APIs, CLI surfaces, and internal contracts may change between releases.

## [0.1.9] — 2026-04-02

### Added
- Evidence-grounded review and follow-up workflows: Vera can now revise and save follow-ups from completed job evidence with distinct workflow paths.
- Live-path characterization tests for evidence-grounded review and follow-up workflows.
- Preview truth guardrail with sanitized_answer fallback for writing-draft turns.

### Changed
- Vera chat() orchestration decomposed: extracted draft content binding, response shaping, and early-exit intent handler dispatch into focused modules (app.py ~1864→~1182 lines, chat() ~1153→~445 lines).
- CLI queue extraction series completed: eight command-family modules extracted (files, health, hygiene, bundle, approvals, inbox, lifecycle, payloads); cli_queue.py reduced from ~909 to ~315 lines.
- Panel extraction: auth-state storage helpers and job-detail section assembly helpers extracted into dedicated modules.
- Governed capability expansion shipped: system inspection lanes, read-only URL investigation, richer file operations, capability semantics contracts, artifact/result shaping for operator evidence.
- Improved preview-only vs submitted reply UX with preview-state notices on writing-draft turns.
- Improved reliability of natural drafting and follow-up conversational paths.
- Polished linked-job review and evidence-grounded follow-up workflows.
- Version-consistency pass: bumped all authoritative and current-version surfaces to 0.1.9.

## [0.1.8] — 2026-03-22

### Changed
- Open-source readiness pass: README, docs, contributor/security surfaces aligned for public release.
- Simplified roadmap to broad directional milestones.
- Added CONTRIBUTING.md and root SECURITY.md.
- Version-consistency pass: bumped all authoritative and current-version surfaces to 0.1.8.
- Updated package metadata, README, ops.md, and version tests to reflect 0.1.8.

## [0.1.7] — 2026-03-10

### Added
- Productized onboarding flow: guided setup slots, OpenRouter live model picker, finish launch options.
- Curated vendor-grouped model catalog for OpenRouter brain configuration.
- Per-slot brain defaults (`primary`, `fast`, `reasoning`, `fallback`) with policy tier framing.

## [0.1.5]

### Added
- Hygiene/recovery baseline: `artifacts prune`, `queue prune`, `queue reconcile`, lock/shutdown hardening.

## [0.1.4]

### Added
- Stability and UX baseline: queue daemon, approvals, mission flows, panel/doctor foundations.
