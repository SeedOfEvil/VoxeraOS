# 05 — Vera Control Layer and Handoff

This document describes the Vera control layer as it currently exists,
split between two packages:

- `src/voxera/vera/` — model-agnostic Vera state, drafting, evidence
  ingestion, investigation flows.
- `src/voxera/vera_web/` — FastAPI web application that exposes the chat
  experience and orchestrates each turn.

The Vera principle is the same as in the project North Star: Vera reasons
freely; only the Voxera queue is allowed to mutate real state. Vera never
executes side effects directly.

## `voxera.vera/` modules

| Module | Purpose |
| ------ | ------- |
| `service.py` | High-level Vera service object exposed to the web app. |
| `prompt.py` | Prompt construction (system + tool framing). |
| `session_store.py` | Persistent session JSON store under `~/VoxeraOS/notes/queue/artifacts/vera_sessions/`. |
| `context_lifecycle.py` | Explicit lifecycle update points for `shared_context` (preview create/revise/rename/clear, handoff, completion ingestion, review, follow-up prep, session clear). |
| `reference_resolver.py` | Bounded session-scoped reference resolution (`draft`, `file`, `job/result`, `continuation`). Conservative and fail-closed. |
| `preview_drafting.py` | Builds the canonical preview payload (`maybe_draft_job_payload`, deterministic and LLM-driven). Routes to code drafts, writing drafts, diagnostics missions, file organization, web/weather investigations. |
| `draft_revision.py` | In-place revision flows for an existing preview draft. |
| `preview_submission.py` | `submit_active_preview_for_session(...)` — writes the preview into the queue inbox via `core.inbox.add_inbox_payload`. |
| `handoff.py` | Higher-level handoff orchestration: validate preview, persist handoff state, register the linked job ref, update shared context. |
| `linked_completions.py` | `ingest_linked_job_completions(...)` — discovers terminal jobs the session is linked to, extracts evidence via `queue_result_consumers.resolve_structured_execution`, classifies surfacing policy, surfaces one-time notifications. |
| `evidence_review.py` | Evidence review surfaces over a single completed job. |
| `result_surfacing.py` | Decides how to present a completed-job result back into chat. |
| `investigation_flow.py` | Web investigation flow (`is_informational_web_query`, `normalize_web_query`, `BraveSearchClient`); stores `last_investigation` in the session. |
| `investigation_derivations.py` | Derived expansions over a stored investigation (expand result, save snippets, etc.). |
| `brave_search.py` | Brave Search HTTP client. |
| `weather.py` / `weather_flow.py` | Open-Meteo client + weather flow (`is_weather_question`, `extract_weather_location_from_message`, `_lookup_live_weather`). |
| `saveable_artifacts.py` | Extracts assistant-authored content (code blocks, prose drafts) into `recent_saveable_assistant_artifacts` (max 8). |

### Session JSON layout

`session_store.py` stores each session under
`~/VoxeraOS/notes/queue/artifacts/vera_sessions/<session_id>.json` where
`session_id` is `vera-<24 hex chars>`. Core fields:

- `turns` — bounded conversation history (max 8 turns).
- `pending_job_preview` — active draft.
- `handoff` — queue submission state.
- `weather_context` — pending/followup state for weather flow.
- `linked_queue_jobs` — bounded registry (max 64) of jobs the session has
  submitted (`job_ref`, `linked_session_id`, `completion_ingested`, ...).
- `shared_context` — bounded continuity object: `active_preview_ref`,
  `last_submitted_job_ref`, `last_completed_job_ref`, `last_saved_file`,
  `active_topic`, `ambiguity_flags`. Always subordinate to canonical truth.
- `recent_saveable_assistant_artifacts` — bounded list of authored content
  the operator could save.
- `routing_debug` — operator-facing trace of dispatch decisions
  (`route_status`, `dispatch_source`, `turn_index`).

## `voxera.vera_web/` modules

| Module | Purpose |
| ------ | ------- |
| `app.py` | FastAPI app and route handlers (`GET /`, `POST /chat`, `GET /chat/updates`, `POST /handoff`, `POST /clear`, `GET /vera/debug/session.json`). |
| `chat_early_exit_dispatch.py` | Pre-LLM short-circuits: diagnostics asks, job review/evidence extraction, investigation derivations, conversational checklists. |
| `execution_mode.py` | Two-mode classifier: `CONVERSATIONAL_ARTIFACT` (planning/checklist/reasoning, zero preview leakage) and `GOVERNED_PREVIEW` (full preview build path). |
| `conversational_checklist.py` | Renders/validates the conversational checklist surface used by `CONVERSATIONAL_ARTIFACT` mode. |
| `draft_content_binding.py` | `extract_reply_drafts()` parses code/text from an LLM reply; `resolve_draft_content_binding()` merges into preview, handles refinements (rename / save-as / refresh failures). |
| `preview_content_binding.py` | Binds preview payload metadata to surfaceable preview content. |
| `response_shaping.py` | Post-LLM sanitization: strips compiler leakage, guards against false preview claims, enforces conversational-mode output. |

### Templates and statics

- `vera_web/templates/index.html` — single Jinja2 template that renders
  the conversation thread, the active preview pane (JSON display + submit
  button), empty-state guidance, and the dev diagnostics panel.
- `vera_web/static/` — static assets (CSS/JS) referenced by the template.

## Chat turn lifecycle

The `POST /chat` handler in `app.py` orchestrates each turn roughly as
follows:

1. Voice ingestion (if voice foundation seam is enabled).
2. Append the operator turn to the session.
3. Run early-exit dispatch:
   - code-draft / writing-draft direct flows
   - diagnostics short-circuits (service status, recent service logs)
   - job review / evidence extraction (`maybe_extract_job_id`)
   - weather flow
   - investigation flow (Brave Search) and investigation derivations
   - conversational checklist short-circuit
4. Decide execution mode (`CONVERSATIONAL_ARTIFACT` vs
   `GOVERNED_PREVIEW`).
5. If `GOVERNED_PREVIEW`, build (or refresh) the canonical preview via
   `vera/preview_drafting.maybe_draft_job_payload`.
6. Generate the LLM reply.
7. Apply guardrails: strip compiler leakage, guard false preview claims,
   enforce conversational checklist output if appropriate.
8. Bind authored content into the preview via `draft_content_binding`.
9. Append assistant turn, capture saveable artifacts.
10. Update `routing_debug` with the dispatch decisions for this turn.

## Handoff to the queue

`POST /handoff` is the only Vera endpoint that mutates queue state:

1. Read the canonical preview from session and validate it against the
   payload supplied in the request.
2. Persist `handoff = {status: "submitted", ...}` into the session.
3. Call `vera/preview_submission.submit_active_preview_for_session(...)`,
   which calls `core.inbox.add_inbox_payload(...)` to drop the payload as
   `inbox/<job>.json`.
4. Extract the `job_id` from the inbox filename stem and register the
   linked job ref into the session via the linked job registry.
5. Call `context_on_handoff_submitted(...)` to update `shared_context`.
6. Clear the session preview post-submit so the next turn starts clean.

The job is now under queue truth — Vera can only observe it via
`vera/linked_completions.ingest_linked_job_completions(...)`, which calls
the canonical `core/queue_result_consumers.resolve_structured_execution`.

## Truth boundaries

The Vera control layer enforces these boundaries strictly:

- Session preview is authoritative for pre-submit state only.
- Once a payload is in `inbox/`, queue lifecycle and artifacts/evidence are
  authoritative; Vera never overrides them.
- Conversation summaries are never authoritative; only the queue and
  artifact surfaces are.
- `shared_context` is a continuity aid only and fails closed when
  ambiguous.

## Operator debug surface

`GET /vera/debug/session.json` returns a snapshot combining:

- Current `turns`, preview, handoff state.
- `shared_context` references.
- `routing_debug` entries for the recent turns.
- `linked_queue_jobs` registry.

This is the surface used by the panel `/vera` view and by integration
tests like `test_vera_session_characterization.py`,
`test_session_routing_debug.py`, and
`test_vera_runtime_validation_fixes.py`.
