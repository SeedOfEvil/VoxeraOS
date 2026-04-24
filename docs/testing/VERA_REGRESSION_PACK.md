# Vera Regression Pack

Compact, repeatable smoke/regression pack for Vera's conversational control layer.

## 1) Purpose

Queue/artifact/panel contracts can be green while Vera drifts in how it drafts, saves, and submits.

This pack protects the high-signal Vera behaviors that operators rely on every day:

- preview creation
- saveability
- truthful submit/no-submit behavior
- queue handoff honesty
- contextual save-by-reference
- investigation-derived save flows
- governed filesystem conversational handoff (find/grep/tree/copy/move/rename)
- typo-like near-submit fail-closed handling (`sned it`, `submt it`, etc.)
- active-preview ambiguity guards for content-replacement turns

## 2) Trust model reminder (must stay true)

- **Vera drafts/reasons/previews**
- **Queue executes**
- **Artifacts prove**

Never treat conversational text as execution truth.

## 3) Service bring-up

From repo root:

```bash
make daemon-restart
systemctl --user restart voxera-panel.service
make vera-restart
make daemon-status
systemctl --user --no-pager status voxera-panel.service
make vera-status
```

## 4) Baseline sanity checks

```bash
voxera doctor --quick
voxera queue status
voxera queue health
voxera queue approvals list
```

Expected baseline:

- services are reachable
- queue is readable
- no unexplained failed-job spikes

## 5) Standard prompt pack (known-good smoke set)

Run these in one fresh Vera session unless noted otherwise.

### A) Code save + submit

Prompt sequence:

1. `Write me a python script that fetches a URL and prints the page title.`
2. `save it`
3. `submit it`

Proves:

- drafting works
- saveability works
- preview submit works
- queue handoff occurs

Expected pass:

- preview exists after step 2
- one queue inbox job appears after step 3
- saved/submitted content is code (not wrapper chatter)

### B) Explanation save + submit

Prompt sequence:

1. `Write me a python script that fetches a URL and prints the page title.`
2. `Explain how this works in plain English.`
3. `save that explanation as script-explained.txt`
4. `submit it`

Proves:

- saveable assistant artifact selection
- explicit save target naming
- truthful submit handoff

Expected pass:

- preview path is `~/VoxeraOS/notes/script-explained.txt`
- preview content is the explanation body (not preview/queue narration)
- one queue job is created on submit

### C) Writing save + submit

Prompt sequence:

1. `Tell me about black holes. Write a short essay.`
2. `save it as black-holes-essay.md`
3. `submit it`

Proves:

- writing flow stays saveable
- note drafting remains stable

Expected pass:

- preview contains essay content
- queue receives one governed write payload on submit

### D) Courtesy-turn save previous answer

Prompt sequence:

1. `Explain photosynthesis simply.`
2. `thanks`
3. `put your previous explanation in a note called photosynthesis.txt`
4. `submit it`

Proves:

- recent meaningful-answer targeting
- courtesy turns do not hijack save targeting

Expected pass:

- preview/file content contains photosynthesis explanation
- preview/file content does **not** contain courtesy filler (e.g., "you're welcome")

### E) Investigation save flow

Prompt sequence:

1. `Search the web for the latest official Brave Search API documentation`
2. `summarize all findings`
3. `save that as brave-api-summary.md`
4. `submit it`

Proves:

- read-only investigation lane remains distinct
- derived investigation output is saveable
- governed save/submit still required for side effects

Expected pass:

- investigation response is read-only findings
- saved content matches derived summary, not shell/wrapper text
- submit creates a real queue job

### F) No-preview fail-safe

Prompt sequence:

1. Start a fresh session with no prior save/preview.
2. `submit it`

Proves:

- truthful no-preview behavior
- no invented queue submission

Expected pass:

- Vera clearly says no governed preview exists
- no queue inbox job is created

### G) Optional compare/expand save flow

Prompt sequence:

1. `Search the web for the latest official Brave Search API documentation`
2. `compare results 1 and 3`
3. `save that to a note`
4. `submit it`

Proves:

- derived follow-up save flow stability

Expected pass:

- preview contains comparison content for selected results
- queue submission occurs only after explicit submit

### H) Filesystem read/discovery via Vera

Prompt sequence:

1. `find txt files in my notes/runtime-validation folder`
2. `search my notes/runtime-validation for "voxera"`
3. `show me the tree for ~/VoxeraOS/notes/runtime-validation`

Proves:

- Vera recognizes governed filesystem read intents conversationally
- preview + submission truth language remains accurate
- queue-backed execution payloads use the filesystem family skills

Expected pass:

- each prompt produces a governed preview (not fake execution confirmation)
- preview steps route to `files.find`, `files.grep_text`, and `files.list_tree`

### I) Filesystem mutating handoff + blocked/missing-source truth

Prompt sequence:

1. `copy ~/VoxeraOS/notes/runtime-validation/src/a.txt to ~/VoxeraOS/notes/runtime-validation/dst/a-copy.txt`
2. `submit it`
3. `move ~/VoxeraOS/notes/runtime-validation/src/b.md to ~/VoxeraOS/notes/runtime-validation/dst/b-moved.md`
4. `submit it`
5. `rename ~/VoxeraOS/notes/runtime-validation/dst/a-copy.txt to a-renamed.txt`
6. `submit it`
7. `show me the tree for ~/VoxeraOS/notes/queue`
8. `copy ~/VoxeraOS/notes/runtime-validation/src/nope.txt to ~/VoxeraOS/notes/runtime-validation/dst/nope-copy.txt` then `submit it`

Proves:

- mutating file actions remain queue-backed (no direct execution lane)
- blocked control-plane scope fails closed conversationally
- missing source errors surface from execution artifacts (not hallucinated)

Expected pass:

- mutating previews route to `files.copy`, `files.move`, and `files.rename`
- each submit creates one queue inbox job
- blocked tree request returns clear refusal with no preview
- missing-source copy fails after execution with truthful artifact-backed messaging

### J) Save-note rename integrity + linked completion binding

Prompt sequence:

1. `Explain earth's core in two short paragraphs.`
2. `save that to a note`
3. `name it earthcore.txt`
4. `submit it`
5. Repeat steps 1-4 in the same session with a different filename.

Proves:

- active preview rename mutates canonical draft state
- submit serializes exactly the visible renamed preview payload
- linked completion is anchored to the newly submitted job, not stale prior job history
- repeated save/submit flows in one session do not leak stale path/job state

Expected pass:

- step 3 preview path updates to `~/VoxeraOS/notes/earthcore.txt`
- step 3 assistant reply explicitly confirms the new destination path
- step 4 queue inbox payload `write_file.path` is exactly `~/VoxeraOS/notes/earthcore.txt`
- completion text for step 4 references the newly submitted job/result (never an earlier filename)
- submit turn does not auto-inject an older unsurfaced linked completion message

### K) Active draft content integrity under prior linked completion history

Prompt sequence:

1. Ensure the session has at least one prior linked completion surfaced in chat.
2. `tell me a funny joke and save it as superfunny.txt`
3. `tell me a hilarious joke` (with the preview still active)
4. `replace content with that`

Proves:

- prior linked-completion status messages are not reused as `write_file.content` by default
- combined generate+save turns bind path and content to the same current intent
- clear generation follow-ups can refresh active draft content while preserving destination
- ambiguous content-replacement requests fail closed and keep draft content unchanged

Expected pass:

- step 2 preview path is `~/VoxeraOS/notes/superfunny.txt` and content is joke text (not linked-job status text)
- for combined generate+save prompts, preview content matches the assistant-authored answer from that same turn (not canned fallback text)
- step 2 preview content does **not** include draft-management wrapper text such as "I added a new joke" or "You can see the current draft"
- step 2 preview content does **not** include readiness/control narration such as "Nothing has been submitted or executed yet" or "I'm ready to submit this to the queue whenever you're set"
- step 3 updates preview content to the new joke and keeps the same path
- if step 3 assistant reply uses wrapper phrasing with a quoted joke, preview stores only the quoted joke body
- step 4 response explicitly states the draft content was left unchanged due to ambiguity

Additional single-turn checks:

- `write a short poem about space and save it as spacepoem.txt` creates preview path `~/VoxeraOS/notes/spacepoem.txt` and content is only the poem body from that same turn (no refusal about missing prior assistant-authored content)
- `write a short poem and save it as poem.txt` strips explanatory tail lines like "You can review the content in the preview pane..."
- poem helper tails like "If you're happy with how it looks ... click submit to save it" are excluded from `write_file.content`
- instructional footers like "If that looks good, just hit Submit to save the file" are excluded from `write_file.content`
- `give me a short summary of Mauna Loa and save it as maunaloa.txt` yields non-empty full summary body only (no wrapper/control lines, no clipped lead/trail text)
- summary meta/control narration like "I've staged a request in the preview pane ..." and "Please review the content ..." must not appear in preview body
- `tell me an astronaut joke and save it as astrojoke.txt` creates preview path `~/VoxeraOS/notes/astrojoke.txt` and content excludes explanatory tail text like "I've drafted a plan..."
- `give me a short volcano fact and save it as volcanofact.txt` stages preview in the same turn (no prior-artifact requirement) and stores only the generated fact body
- content-type matrix (`joke`, `poem`, `fact`, `summary`) with combined generate+save binds authored body text, never wrapper/control text like "I've drafted..." or "prepared a preview..."
- active-draft refresh with unquoted wrapper text and contractions (for example: "Why don't ... They don't ...") preserves the full joke body and strips wrapper/meta narration
- submit payload `write_file.content` exactly matches the pure preview content shown before submit

### O) Active-draft content refresh

Prompt sequence:

1. `write a short poem and save it as poem.txt`
2. `generate a different poem`
3. `send it`

Proves:

- clear content-refresh requests on an active preview replace the body
- preview path is preserved during refresh
- refreshed content is pure authored body (no helper/control narration)
- submit after refresh uses the refreshed body exactly

Expected pass:

- step 1 creates preview with path `~/VoxeraOS/notes/poem.txt` and poem body
- step 2 updates preview content to a different poem body
- step 2 preserves path as `~/VoxeraOS/notes/poem.txt`
- step 2 content does NOT contain helper text ("Updated the draft...", "You can review...", etc.)
- step 3 submits the refreshed body exactly as previewed

Also test variants:
- `tell me a different joke and add it as content` (joke refresh)
- `give me a shorter summary` (summary refresh)
- `give me a different fact` (fact refresh)

### P) Active-draft ambiguous change request fail-closed

Prompt sequence:

1. Create any preview (e.g., `write a poem and save it as poem.txt`)
2. `change it`

Proves:

- ambiguous change requests fail closed
- preview content remains unchanged
- no fake content mutation

Expected pass:

- step 2 leaves preview content identical to step 1
- step 2 response explicitly mentions the draft was left unchanged or the request was ambiguous
- no helper/control text injected as content

Also test variants: `make it better`, `fix it`

## 6) Verification steps per scenario

Use these checks after each scenario:

1. **Preview truth**
   - confirm preview appears only when save/write intent exists
2. **Queue truth**
   - confirm queue inbox job exists for submit scenarios
   - confirm no job exists for no-preview submit
3. **Artifact/content truth**
   - inspect saved note payload/content
   - ensure content is meaningful answer/derivation, not wrapper text
4. **Follow-up honesty**
   - Vera follow-up text should not claim execution success without queue/artifact evidence

Helpful commands:

```bash
voxera queue status
voxera queue health
find "${VOXERA_QUEUE_ROOT:-$HOME/VoxeraOS/notes/queue}/inbox" -maxdepth 1 -name '*.json'
```

## 7) Pass/fail criteria

Pass when all are true:

- save-intent prompts create meaningful previews
- submit uses queue handoff and clears active preview
- save-by-reference targets meaningful prior content
- investigation-derived outputs are saveable and clean
- no-preview submit fails clearly/truthfully with zero handoff

Regression if any occur:

- fake submit success without queue handoff
- courtesy/wrapper text saved instead of requested content
- no-preview submit creating queue jobs
- investigation lane causing side effects without governed save+submit

### L) Submit-intent strictness / typo-like near-submit phrasing

Prompt sequence:

1. Create any preview (e.g., `write a poem and save it as poem.txt`)
2. `send iit`

Proves:

- typo-like near-submit phrases do not trigger queue handoff
- Vera explicitly states no submission occurred
- no fake conversational submit acknowledgment

Expected pass:

- step 2 response explicitly says the preview was NOT submitted
- preview remains active (not cleared)
- no queue inbox job is created
- subsequent `send it` (correct spelling) performs the real canonical submit

### M) Post-preview informational rename mutation

Prompt sequence:

1. `tell me about the biggest volcano on earth`
2. `save it to a note`
3. `call it volcano.txt`

Proves:

- clear rename instructions after preview creation update the canonical path
- preview content is preserved across rename
- no stale auto-generated filename remains

Expected pass:

- step 2 creates preview with auto-generated `note-TIMESTAMP.txt` path
- step 3 updates preview path to `~/VoxeraOS/notes/volcano.txt`
- step 3 preserves original content
- assistant reply confirms the new filename/path

Also test variants: `name it volcano.txt`, `rename it to volcano.txt`

### N) Summary preview-body purity

Prompt sequence:

1. `give me a short summary of Mauna Loa and save it as maunaloa.txt`

Proves:

- summary-type generate+save flows produce pure authored content
- helper/control narration is stripped from preview body
- no "You can review..." or "Please review..." preamble in content

Expected pass:

- preview path is `~/VoxeraOS/notes/maunaloa.txt`
- preview content contains the summary body only
- preview content does NOT contain helper/control text such as:
  - "You can review the content..."
  - "Please review the content..."
  - "authorize the file creation"
  - "preview pane"

### Q) Referenced prior answer binding + content inspection + empty-content submit guard

Prompt sequence:

1. `Good morning Vera, give me a one sentence status check.`
2. `Tell me a short story about spacetime.`
3. `Create a file called typed-smoke-test.txt containing exactly what you just said`
4. `Where is the content?`
5. `submit it`

Proves:

- "what you just said" binds the latest meaningful assistant answer into `write_file.content` (no empty shell).
- "Where is the content?" answers deterministically — path + content preview — with no LLM call and no vague "unchanged" wording.
- An empty `write_file.content` cannot submit unless the goal explicitly requests an empty file.

Expected pass:

- step 3 preview path is `~/VoxeraOS/notes/typed-smoke-test.txt` and content contains the step-2 answer (non-empty).
- step 4 response shows the path and a quoted content preview (truncates long bodies with a clear marker) — never responds "the draft was unchanged" vaguely.
- step 5 submits via queue (non-empty content is allowed through the guard).

Failure modes that must remain blocked:

- empty write preview + `submit it` → fails closed with `handoff_empty_content_blocked`, queue inbox untouched, preview preserved.
- explicit empty-file intent (`create an empty file called x.txt`, `touch x.txt`) → still allowed through the guard.
- wrapper text ("I've prepared a preview…", "the draft is unchanged", "Your linked job completed…") → never saved as an authored artifact and never bound into `write_file.content`.

### R) Active-preview append / expand truthful mutation

Prompt sequence:

1. `Good afternoon Vera, do you mind telling me a bunch of dad jokes?`
2. `save this to a note`
3. `name it jokiez.txt`
4. `add 10 more jokes to the list`
5. `Where is the content?`
6. `submit it`

Proves:

- additive follow-ups ("add N more X", "append N more", "continue the list", "expand it with more", "make it longer") mutate the active write_file preview truthfully, preserving the path.
- Vera never claims "I've added N jokes", "expanded the list", or "this brings the total to M" unless the active preview payload actually changed.
- A typo-tolerant variant ("add 10 more jokees") behaves the same way.
- A rename immediately preceding the append preserves the renamed path — the append never silently renames back.

Expected pass:

- step 4 preview path is `~/VoxeraOS/notes/jokiez.txt` (unchanged from step 3).
- step 4 preview content contains the original jokes AND the new jokes (no duplication, no loss of original body).
- step 4 response either shows an "updated the preview" message or honestly says the draft was left unchanged — never a false count claim.
- step 5 content inspection shows the combined body.
- step 6 submitted payload contains the combined body.

Failure modes that must remain blocked:

- LLM asserts "I've added 20 jokes" / "appended 5 bullets" / "this brings the total to 30" while the binding did NOT mutate the preview → response shaping replaces or overrides the LLM text with an honest "draft unchanged" message.
- LLM reply is pure wrapper/status narration → binding rejects it, preview unchanged, honest fail-closed message shown.
- Ambiguous "add it" / "add that" / "change it" → no expand binding, no false-success reply (existing ambiguity guard applies).

## 8) Automated coverage anchors

The pack is represented by focused Vera tests (not one giant mixed-flow test):

- `tests/test_vera_web.py`
- `tests/test_vera_contextual_flows.py`
- `tests/test_vera_session_characterization.py`
- `tests/test_vera_runtime_validation_fixes.py`
- `tests/test_vera_preview_content_truth.py` (content truth sweep — section Q above)

Prefer extending those focused files for future Vera regression additions.
