# Capability: Web Investigation Rules

Web investigation is a read-only informational lane.

## Allowed Scope
- informational queries
- query normalization for clearer retrieval
- source gathering and synthesis for conversational responses

## Disallowed Scope
- no side effects
- no downloads
- no direct preview creation from a standalone informational turn
- no queue creation for informational research

Operational requests still belong to the Voxera execution path.
Informational responses should not mention Voxera planning/execution by default.

## Enrichment-to-Preview Bridge
When an active draft preview exists and the user makes an informational query (e.g. "find the latest news"), the service layer performs a read-only web enrichment and stores the result as temporary `last_enrichment` session state.

Rules:
- Enrichment is only stored when `pending_preview is not None` — standalone informational turns with no active preview do NOT trigger enrichment storage.
- The hidden compiler may receive `enrichment_context` (query, summary, retrieved_at_ms) as read-only input context.
- Pronoun follow-ups like "put that into the file" may resolve against the enrichment summary to mutate `write_file.content` in the active preview.
- If no enrichment is available and the pronoun reference is ungrounded, fail closed: return `no_change`.
- Enrichment is read-only input to the compiler — it never writes files or creates queue work on its own.
