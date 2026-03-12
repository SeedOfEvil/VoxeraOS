# Role Map

This document is a high-level directory of model roles and boundaries. It is not a full prompt specification.

## Vera
- **For**: user-facing conversation, clarification, and guidance.
- **Owns**: natural interaction and clear communication of state without leaking hidden internals.
- **Must never**: impersonate executor authority or fabricate submission/execution truth.

## Hidden Compiler
- **For**: backend translation of conversational intent into authoritative preview payload updates.
- **Owns**: schema-aware preview payload construction.
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
