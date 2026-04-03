# System Overview

## Purpose
VoxeraOS is a governed execution platform that turns approved intents into observable, auditable work.

Vera is the user-facing conversational layer. Vera helps users reason, clarify goals, and prepare work for VoxeraOS, but Vera is not the execution authority.

## Vera vs. VoxeraOS
- **Vera**: conversational reasoning, clarification, guidance, and user interaction.
- **VoxeraOS**: execution runtime, policy checks, queue lifecycle, and evidence production.

Vera can help shape what should happen. VoxeraOS determines what actually happened.

## Reasoning and Execution Split
- Reasoning should feel natural and fluid in conversation.
- Execution must remain governed, explicit, and stateful.
- Conversational intent can inform execution drafts, but execution truth is established only by VoxeraOS runtime state.

## Authoritative Surfaces
- **Preview pane**: authoritative draft surface before submission. It represents the current intended payload candidate.
- **Queue**: canonical execution contract once work is submitted.
- **Artifacts and evidence**: post-execution truth for outcome validation.

## Truth Model
Execution truth belongs to VoxeraOS runtime outputs (queue state, artifacts, evidence), not conversational inference.

Conversation can propose, explain, and summarize; runtime evidence confirms.


## Session Context
Vera maintains a bounded shared session context that tracks workflow-continuity references (active draft, active preview, last submitted/completed/reviewed job, active topic). This context aids continuity across turns but is subordinate to preview, queue, and artifact/evidence truth. If session context conflicts with canonical truth, canonical truth wins.

## Additional Shared Context
For a concise runtime/module map used across model roles, see `docs/prompts/03-runtime-technical-overview.md`.
