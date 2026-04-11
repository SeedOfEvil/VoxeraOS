# 05 — Vera Control Layer and Handoff

Vera is the conversational control surface in VoxeraOS. She is explicitly **not** the execution runtime. Her job is to understand intent, draft a governed preview, hand off a truthful payload into the queue, and then review evidence from the queue after execution.

This document describes Vera's current shape, grounded in `src/voxera/vera/` and `src/voxera/vera_web/`.

## Two Vera surfaces

1. **Vera web app** — `src/voxera/vera_web/app.py`. FastAPI app (served by `voxera vera`, `make vera`, or `voxera-vera.service`). This is the chat UI.
2. **Vera service layer** — `src/voxera/vera/*.py`. Reusable conversational logic. The web app is a thin adapter on top; the service layer can also be driven from tests or future surfaces.

## Vera's responsibilities

Vera's contract, read directly from `vera/service.py`, `preview_drafting.py`, `preview_submission.py`, `evidence_review.py`, and `linked_completions.py`:

- **Understand intent** — natural-language routing with conservative defaults and fail-closed behavior.
- **Draft a governed preview** — deterministic preview drafting for well-known shapes (file write, file organize, mission, web investigation, weather, draft revisions, saveable artifacts).
- **Submit truthfully** — only claim submission after the queue acknowledges intake; never claim execution before the queue says so.
- **Review outcomes** — when asked about a prior job, ground the answer in canonical queue evidence (`resolve_structured_execution`), not chat memory.
- **Surface linked completions** — track jobs submitted from the session and surface their terminal state into chat at a principled moment.

Vera is intentionally conservative about turning conversation into execution. If the session state is ambiguous, her default is to ask / fail closed rather than silently fabricate a submission.

## Vera session state

Sessions are persisted at `~/VoxeraOS/notes/queue/artifacts/vera_sessions/<session_id>.json` by `vera/session_store.py`. The store is bounded — `MAX_SESSION_TURNS = 8` for prompt context, with additional bounded tracking for:

- linked jobs (`_MAX_LINKED_JOB_TRACK = 64`)
- linked completions (`_MAX_LINKED_COMPLETIONS = 64`)
- linked notifications (`_MAX_LINKED_NOTIFICATIONS = 128`)
- saveable assistant artifacts (`_MAX_SAVEABLE_ASSISTANT_ARTIFACTS = 8`)

Important fields tracked per session:

- conversation turns (user + assistant)
- active preview (shape, payload, ambiguity flags)
- `shared_context` continuity object (active draft, active preview, last submitted / completed / reviewed job, last saved file, active topic)
- `linked_job_registry` — `{tracked, completions, notification_outbox}`
- session-level voice flags (when set)

`shared_context` is updated through explicit lifecycle update points in `vera/context_lifecycle.py`:

- `context_on_preview_created`
- `context_on_preview_cleared`
- `context_on_handoff_submitted`
- `context_on_completion_ingested`
- `context_on_review_performed`
- `context_on_followup_preview_prepared`
- `context_on_session_cleared`

Canonical truth surfaces (preview / queue / artifacts-evidence) always win over shared context. When continuity is ambiguous, the session fails closed.

## Preview ownership and routing lane precedence

Two dedicated modules narrow the surfaces that can mutate Vera's preview state:

- `vera/preview_ownership.py` owns every transition into the session's active preview slot. Its public surface is `reset_active_preview`, `record_followup_preview`, `clear_active_preview`, and `record_submit_success`. Each helper performs the coupled writes that used to live scattered throughout `app.py` (preview payload + `preview_ready` handoff marker + shared-context refresh) as a single atomic unit, so there is one place to audit how previews are created, revised, replaced, cleared, and cleaned up.
- `vera_web/preview_routing.py` documents the canonical routing lanes that may claim a chat turn. `canonical_preview_lane_order()` returns the lane tuple that the top-level dispatch in `chat()` must keep in sync with its branch order. The enum values are also used by `is_active_preview_revision_turn`, the conservative gate that protects normal active previews from being hijacked by unrelated lanes.

Two lane areas that grew large enough to crowd `app.py` were extracted into dedicated modules under `vera_web/lanes/` as a small, targeted decomposition — **only the automation and review lanes** were moved; older stable lanes still live inline in `app.py`:

- `vera_web/lanes/automation_lane.py` owns the automation preview submit, automation draft/revise, automation lifecycle, and automation-shell materialization branches. Each lane entry point returns an `AutomationLaneResult` with a simple matched/not-matched contract and performs its own preview-state writes exclusively through `preview_ownership` helpers. Detectors (`_detect_automation_clarification_completion`, `_looks_like_direct_automation_request`, `_looks_like_previewable_automation_intent`, `_synthesize_direct_automation_preview`) live next to the lane logic they feed.
- `vera_web/lanes/review_lane.py` owns `compute_active_preview_revision_in_flight` (the canonical gate plus the review/evidence belt-and-suspenders that keeps ambiguous save/revise-from-evidence phrases from hijacking a normal active preview) and `apply_early_exit_state_writes` (the preview / context / derived-output write choreography that runs when `dispatch_early_exit_intent` returns `matched=True`). Preview mutations still flow through `preview_ownership` helpers — the review lane module never calls `write_session_preview` directly.

`app.py` remains the top-level orchestrator. It still owns lane order, calls each lane entry point in canonical precedence, and performs the final `append_session_turn` / routing-debug / `_render_page` orchestration. The extraction deliberately does **not** introduce a generic lane framework; it is a bounded decomposition of two recent, high-growth lanes.

Canonical lane order (defined in `preview_routing.canonical_preview_lane_order`):

1. `EXPLICIT_SUBMIT` — submit / handoff on the active preview (including automation-preview-save).
2. `ACTIVE_PREVIEW_REVISION` — revision or follow-up mutation on the active preview. This lane is the only path that rewrites the payload in place on an active preview for normal `write_file` previews and for automation previews.
3. `AUTOMATION_LIFECYCLE` — manage saved automation definitions. Steps aside when a normal active preview is clearly under revision.
4. `FOLLOWUP_FROM_EVIDENCE` — evidence-driven follow-up previews drafted from a completed job.
5. `PREVIEW_CREATION` — code/writing/automation shell synthesis, deterministic builder path, rename/save-as fallback.
6. `READ_ONLY_EARLY_EXIT` — time, weather, diagnostics refusal, blocked file intent, near-miss submit, investigation utilities.
7. `CONVERSATIONAL` — LLM orchestration + post-LLM draft binding.

Each lane either claims the turn cleanly or fails closed and allows later lanes to try. A lane must not silently mutate a preview that is owned by another lane; the revision-turn gate is the explicit short-circuit that enforces this for normal previews.

The revision-turn gate is threaded into `chat_early_exit_dispatch.dispatch_early_exit_intent` as an explicit `active_preview_revision_in_flight` parameter. When it is true, the evidence-driven preview-writing branches (follow-up from evidence, save-follow-up, revise-from-evidence, investigation derived-save, investigation save) are skipped so they cannot clobber the active preview. The non-mutating branches (time question, diagnostics refusal, job review report, near-miss submit rejection, stale-draft reference) still run. The flag is computed once in `lanes/review_lane.compute_active_preview_revision_in_flight`, which wraps the narrow `is_active_preview_revision_turn` gate with a belt-and-suspenders layer: when a normal active preview is present, ambiguous `is_save_followup_request` / `is_revise_from_evidence_request` / `is_investigation_save_request` / `is_investigation_derived_save_request` matches are also treated as revision candidates. This catches phrases like "save the follow-up as a file", "update that based on the result", and "make it save the results to a file" that the narrow revision gate does not cover. `app.py` reads the computed flag and forwards it into the early-exit dispatch and the automation-lifecycle lane.

## Preview model

Preview is the pre-submit draft object. It is Vera's authoritative surface **before** submit and is also the exact source of the payload that goes to the queue at submit time.

Preview shapes come from `vera/preview_drafting.py`:

- **File write** — write authored text content to a bounded notes path.
- **File organize** — structured `file_organize` contract for copy/move workflows.
- **Mission** — named mission by `mission_id`.
- **Goal** — natural-language goal routed through planner flow.
- **Web investigation** — read-only investigation via the Brave client (`vera/brave_search.py`, `vera/investigation_flow.py`).
- **Weather** — bounded weather lookup (`vera/weather.py`, `vera/weather_flow.py`).
- **Draft revision** — rename / save-as / content rewrite of an active preview (`vera/draft_revision.py`).
- **Saveable assistant artifact** — save a recent assistant content block under a governed path (`vera/saveable_artifacts.py`).
- **Automation definition** — governed automation preview shape drafted by `vera/automation_preview.py`. Includes title, trigger_kind, trigger_config, payload_template, enabled flag, and an operator-facing explanation. Submit saves a durable automation definition via the automation store — it does NOT emit a queue job. Execution happens only through the automation runner → queue path. Supported authoring triggers: `delay`, `recurring_interval`, `once_at`. Preview clearly communicates that saving is distinct from executing.
- **Automation lifecycle management** — conversational management of saved automation definitions via `vera/automation_lifecycle.py`. Vera can show, enable, disable, delete, force-run, and surface history for saved automations through natural conversation ("show me that automation", "disable it", "did it run?"). Reference resolution is fail-closed: ambiguous or missing references prompt for clarification. Enable/disable mutate the saved definition. Delete removes the definition but preserves history. Run-now uses the existing automation runner → queue path — Vera does not execute payloads directly.
- **Time-aware context** — `vera/time_context.py` provides deterministic helpers for current local/UTC time, elapsed-time formatting, time-until formatting, and relative-day classification. Automation lifecycle responses (show, history) use human-readable absolute + relative phrasing ("today at 3:15 PM (about 47 minutes ago)"). Simple time/date/timezone questions ("what time is it?") are answered deterministically from the system clock via early-exit dispatch. The Vera system prompt and operator assistant prompt both receive a structured time-context block with current time information.

Key rules observable in the code:

- If preview state is ambiguous (unclear target path, unclear content source, mixed intent), Vera fails closed.
- Accepted naming mutations (rename / save-as) explicitly confirm the new destination path in the preview body before submit eligibility.
- Authored content captured in the same turn can bind to a same-turn preview without prior artifacts, but only when the intent is clearly a single-turn generate+save request.
- Linked-completion status text and narration wrappers are **not** eligible as default note-body content.

## Submit path

`vera/preview_submission.py` handles submit detection, payload normalization, and queue handoff acknowledgement. The flow:

1. Detect that the user's current turn is an explicit submit / "go ahead" / "send it" relative to the active preview. Ambiguous wording is rejected.
2. Normalize the preview payload into a canonical queue payload.
3. Hand the payload to the queue intake.
4. Wait for queue acknowledgement (the job file lands in `inbox/` or further along the lifecycle).
5. Only then emit a confirmation in chat — Vera never claims submission before the queue has acknowledged.

## Review path

`vera/evidence_review.py` handles "what happened with that job" questions. It:

- Resolves the job reference (explicit id, most recent linked job, or shared-context pointer via `vera/reference_resolver.py`).
- Calls `core/queue_result_consumers.resolve_structured_execution(...)` to get the canonical terminal outcome, step results, artifacts, and review summary.
- Renders a compact, evidence-grounded summary — not a hallucinated recap.

If no reviewable job can be resolved, Vera refuses to summarize. It does not fabricate an outcome from chat history.

## Linked completions

`vera/linked_completions.py` is the session-local linked-job registry:

- `ingest_linked_job_completions(...)` — absorb terminal lifecycle + normalized completion payloads for any linked jobs.
- `maybe_deliver_linked_completion_live(...)` / `maybe_deliver_linked_completion_live_for_job(...)` — deliver a completion into the current chat stream when appropriate.
- `maybe_auto_surface_linked_completion(...)` — surface the latest terminal outcome at a principled moment in a subsequent turn.

Surfacing policy is intentionally conservative:

- read-only success → next chat cycle surfaces one concise success message, not reposted later
- approval-blocked → one concise "waiting for approval" message
- failed → concise failure summary (and next-action hint when available)
- mutating success → concise confirmation, only if truly terminal
- session files track `surfacing_policy`, `surfaced_in_chat`, and `surfaced_at_ms`

This is why fresh Vera chats are recommended when testing linked-completion behavior — stale sessions can already carry surfaced flags for prior jobs.

## Investigation and weather flows

- `vera/investigation_flow.py` handles informational web queries through the Brave search client. It produces structured results and a formatted answer, and it is explicitly read-only.
- `vera/investigation_derivations.py` handles derived follow-ups — compare, summarize, expand, and save-as operations against the last investigation.
- `vera/weather_flow.py` + `vera/weather.py` handle live weather lookups through Open-Meteo with conservative follow-up continuity (waiting for a location, clarifying ambiguity).

These flows are good examples of how Vera can return meaningful content without touching the execution queue — they are read-only conversational surfaces, not execution.

## Hidden compiler seam

`vera/service.py::HiddenCompilerDecision` is a structured decision shape used when the Vera prompt asks the brain to make a preview update decision. Actions are bounded:

- `replace_preview` — replace with a new preview dict
- `patch_preview` — apply a bounded patch
- `no_change` — decline to update

Intent class is bounded to `new_intent | refinement | unclear`. Any payload that does not match the strict schema is rejected. This is one of the red-team targets (`tests/test_vera_hidden_compiler.py`, `test_vera_compiler_leakage.py`).

## Vera web routes

From `src/voxera/vera_web/app.py`:

- `GET /` — chat UI shell.
- `POST /chat` — main chat turn handler.
- `GET /chat/updates` — pull updates / linked completion delivery.
- `POST /handoff` — explicit handoff/submit path. Routes through the correct preview-type-specific submit path: automation definition previews save a durable definition (no queue job); normal action previews submit a queue job.
- `POST /clear` — clear the session.
- `GET /vera/debug/session.json` — debug snapshot of the current session.

Vera's UI is Jinja-rendered (`vera_web/templates/`, `vera_web/static/`). The mutation guard, CSRF cookie, and request-value helpers are shared with the panel where applicable.

## Extraction map for Vera internals

The refactors that preceded this bundle split Vera's reply orchestration into narrower modules. Current ownership:

| Concern | Module |
|---|---|
| Top-level `generate_vera_reply`, provider selection, linked-job delivery | `vera/service.py` |
| System prompt + preview builder prompt + queue-boundary summary | `vera/prompt.py` |
| Session persistence + active preview state | `vera/session_store.py` |
| Deterministic preview drafting + drafting guidance | `vera/preview_drafting.py` |
| Active preview rename / path / content follow-up | `vera/draft_revision.py` |
| Submit detection + payload normalization + handoff ack | `vera/preview_submission.py` |
| Thin compatibility façade across handoff seams | `vera/handoff.py` |
| Evidence-grounded review flow | `vera/evidence_review.py` |
| Linked-job registry + auto-surfacing | `vera/linked_completions.py` |
| Result-forward text extraction | `vera/result_surfacing.py` |
| Saveable recent assistant content | `vera/saveable_artifacts.py` |
| Shared session context lifecycle points | `vera/context_lifecycle.py` |
| Centralized preview state transitions (create/revise/clear/submit) | `vera/preview_ownership.py` |
| Canonical preview routing lane enum + revision-turn gate | `vera_web/preview_routing.py` |
| Bounded reference resolution (draft/file/job/continuation) | `vera/reference_resolver.py` |
| Automation definition preview drafting, revision, submit-to-store | `vera/automation_preview.py` |
| Conversational lifecycle management for saved automations | `vera/automation_lifecycle.py` |
| Explicit read-only web investigation | `vera/investigation_flow.py` |
| Derived follow-up handling for investigation | `vera/investigation_derivations.py` |
| Live weather routing + continuity | `vera/weather_flow.py` + `vera/weather.py` |
| Brave Search API client | `vera/brave_search.py` |

When extending Vera, prefer adding to one of the dedicated modules above rather than re-growing `handoff.py` or `service.py`. The compatibility façade is intentionally thin.

## Integrity invariant (from README)

> Visible preview state is authoritative pre-submit and is the exact source for queued payload serialization; ambiguous preview state fails closed; accepted naming mutations explicitly confirm the new destination path; linked completion surfacing prioritizes the latest linked submit in-session; clear single-turn generate+save requests can bind same-turn authored content without requiring prior artifacts; linked-completion status text and draft-management/explanatory wrapper narration are not eligible default note-body content.

That sentence is the condensed contract. When making changes to Vera, preserve it.

## AI instruction surfaces

Vera's system prompt is composed from structured markdown documents under `docs/prompts/`. The composition engine (`src/voxera/prompts.py`) assembles shared system docs, role-specific docs, and capability docs into a single prompt per model role. Prompt surfaces refreshed as of the current version:

- **Shared system docs** (`00-system-overview.md` through `03-runtime-technical-overview.md`) — automation subsystem awareness, truth model updated for automation definitions.
- **Role docs** (`roles/vera.md`, `roles/planner.md`, etc.) — automation lifecycle management, output quality expectations, plan quality guidance.
- **Capability docs** (`capabilities/output-quality-defaults.md`, etc.) — cross-surface output quality defaults, automation-aware lifecycle and evidence docs.
- **Code-level hints** (`vera/service.py` `_CODE_DRAFT_HINT`, `_WRITING_DRAFT_HINT`) — expanded guidance for code completeness and writing depth.
- **Operator assistant prompt** (`operator_assistant.py`) — automation awareness, precise lifecycle terms, depth-responsive advisory tone.

The prompt composition system wires `capabilities/output-quality-defaults.md` to all five model roles so that output quality guidance applies consistently across Vera, the hidden compiler, the planner, the verifier, and the web investigator.
