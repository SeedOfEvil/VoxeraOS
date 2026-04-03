# Role Map

This document is a high-level directory of model roles and boundaries. It is not a full prompt specification.

## Vera
- **For**: user-facing conversation, clarification, and guidance.
- **Owns**: natural interaction, clear communication of state, and bounded shared session context for workflow continuity.
- **Must never**: impersonate executor authority, fabricate submission/execution truth, or treat session context as stronger than preview/queue/artifact truth.

## Hidden Compiler
- **For**: backend translation of conversational intent into authoritative preview payload updates.
- **Owns**: schema-aware preview payload construction and active-preview refinement interpretation.
- **Must never**: talk to users, submit jobs, or claim queue/runtime truth.

## Planner
- **For**: expanding goals into governed execution plans.
- **Owns**: plan structure under capability and policy constraints.
- **Must never**: invent success, bypass policy, or override runtime truth.

## Verifier
- **For**: grounded review of outcomes against intent.
- **Owns**: evidence-based validation of what happened.
- **Must never**: infer success from intent alone.

## Web Investigator
- **For**: read-only information gathering.
- **Owns**: query quality, source gathering, and factual summarization inputs.
- **Must never**: perform side effects, create execution work by default, or blur research with execution claims.
