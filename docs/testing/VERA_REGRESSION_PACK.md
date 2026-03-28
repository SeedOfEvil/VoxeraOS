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
- step 4 queue inbox payload `write_file.path` is exactly `~/VoxeraOS/notes/earthcore.txt`
- completion text for step 4 references the newly submitted job/result (never an earlier filename)
- submit turn does not auto-inject an older unsurfaced linked completion message

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

## 8) Automated coverage anchors

The pack is represented by focused Vera tests (not one giant mixed-flow test):

- `tests/test_vera_web.py`
- `tests/test_vera_contextual_flows.py`
- `tests/test_vera_session_characterization.py`

Prefer extending those focused files for future Vera regression additions.
