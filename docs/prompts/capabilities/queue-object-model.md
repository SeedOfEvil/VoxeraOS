# Capability: Queue Object Model

Use this as the short-form contract for execution truth.

## Core Objects
- **Preview object**: authoritative draft only before submit.
- **Queue job object**: authoritative submitted execution unit after submit.
- **Artifacts**: durable runtime outputs tied to a job.
- **Evidence**: runtime-grounded subset used to determine what actually happened.

## Truth Hierarchy
1. Conversational truth (never runtime-authoritative)
2. Preview truth (pre-submit only)
3. Queue truth (submitted lifecycle state)
4. Artifact/evidence truth (post-execution outcome proof)


- Submitted jobs should be interpreted with explicit execution capability declarations (side effects, scope, secrets, isolation, expected artifacts).

## Reviewer Rule
Verifier/reviewer must base conclusions on terminal queue state plus runtime artifacts/evidence, not on intent quality or conversational confidence.
