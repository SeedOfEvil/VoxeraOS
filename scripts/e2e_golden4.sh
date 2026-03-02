#!/usr/bin/env bash
set -euo pipefail

cd ~/VoxeraOS
source .venv/bin/activate

# --- 0) Update/install + restart services (safe even if already up) ---
python3 -m pip install -e .
systemctl --user restart voxera-daemon.service voxera-panel.service

# --- 1) Stop services so we can clean state safely ---
systemctl --user stop voxera-daemon.service voxera-panel.service

# --- 2) Archive existing queue + artifacts (clean slate) ---
ts=$(date +%Y%m%d-%H%M%S)

mkdir -p notes/queue/_archive/"$ts"
[ -d notes/queue/done ]    && mv notes/queue/done    notes/queue/_archive/"$ts"/ || true
[ -d notes/queue/failed ]  && mv notes/queue/failed  notes/queue/_archive/"$ts"/ || true
[ -d notes/queue/pending ] && mv notes/queue/pending notes/queue/_archive/"$ts"/ || true

mkdir -p notes/queue/{pending,done,failed}
rm -f notes/queue/job-*.json notes/queue/*.pending.json 2>/dev/null || true

mkdir -p ~/.voxera/_archive/"$ts" 2>/dev/null || true
[ -d ~/.voxera/artifacts ] && mv ~/.voxera/artifacts ~/.voxera/_archive/"$ts"/ || true
mkdir -p ~/.voxera/artifacts

# --- 3) Start services again ---
systemctl --user start voxera-daemon.service voxera-panel.service

# --- 4) Create the golden 4 jobs ---
cat > notes/queue/job-sandbox-string.json <<'JSON'
{ "id":"sandbox-string", "goal":"Run sandbox.exec to print HELLO from inside the sandbox." }
JSON
cat > notes/queue/job-sandbox-argv.json <<'JSON'
{ "id":"sandbox-argv", "goal":"Run sandbox.exec with argv form to print HELLO-ARGV." }
JSON
cat > notes/queue/job-write-notes.json <<'JSON'
{ "id":"write-notes", "goal":"Write the text 'OK' to a notes file under the allowed notes directory." }
JSON
cat > notes/queue/job-e2e-open.json <<'JSON'
{ "id":"e2e-open", "goal":"Open https://example.com in the controlled browser." }
JSON

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

QUEUE_ROOT="$(realpath notes/queue)"
JOB_REF="e2e-open"
JOB_STEM="job-${JOB_REF}"

# Filesystem-based paths — these are deterministic and don't rely on CLI
# table parsing or ANSI-output heuristics.
APPROVAL_ARTIFACT="${QUEUE_ROOT}/pending/approvals/${JOB_STEM}.approval.json"
DONE_FILE="${QUEUE_ROOT}/done/${JOB_STEM}.json"
FAILED_DIR="${QUEUE_ROOT}/failed"

# Panel port: prefer env var (matches daemon startup), fall back to default.
PANEL_PORT="${VOXERA_PANEL_PORT:-8844}"

queue_status() { ./.venv/bin/voxera queue status; }

bucket_count() {
  local bucket="$1"
  local out line
  out="$(queue_status || true)"
  line="$(printf "%s\n" "$out" | rg "│\s*${bucket}\s*│" | head -n1 || true)"
  [ -z "$line" ] && { echo 0; return 0; }
  printf "%s\n" "$line" | awk -F'│' '{gsub(/[[:space:]]/,"",$3); print $3}'
}

wait_until() {
  local timeout_s="$1"; shift
  local sleep_s="$1"; shift
  local desc="$1"; shift
  local cond="$1"; shift || true
  local start now
  start="$(date +%s)"
  while true; do
    if eval "$cond"; then return 0; fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$timeout_s" ]; then
      echo "TIMEOUT: $desc"
      return 1
    fi
    sleep "$sleep_s"
  done
}

print_counts() {
  echo "counts: pending=$(bucket_count "pending/") approvals=$(bucket_count "pending/approvals/") done=$(bucket_count "done/") failed=$(bucket_count "failed/")"
}

# Filesystem-based: is the approval artifact present for our job?
approval_artifact_present() {
  [ -f "${APPROVAL_ARTIFACT}" ]
}

# Filesystem-based: has the job reached a terminal state (done or failed)?
# Checks both exact filename and timestamped collision variants.
job_in_done() {
  [ -f "${DONE_FILE}" ] || \
    ls "${QUEUE_ROOT}/done/${JOB_STEM}"*.json 2>/dev/null | head -n1 | grep -q .
}

job_in_failed() {
  [ -f "${FAILED_DIR}/${JOB_STEM}.json" ] || \
    ls "${FAILED_DIR}/${JOB_STEM}"*.json 2>/dev/null | head -n1 | grep -q .
}

dump_diag() {
  echo "--- queue status ---"
  queue_status || true
  echo "--- approvals list ---"
  ./.venv/bin/voxera queue approvals list || true
  echo "--- pending dir ---"
  ls -la "${QUEUE_ROOT}/pending/" 2>/dev/null || true
  echo "--- approvals dir ---"
  ls -la "${QUEUE_ROOT}/pending/approvals/" 2>/dev/null || true
  echo "--- done dir ---"
  ls -la "${QUEUE_ROOT}/done/" 2>/dev/null || true
  echo "--- failed dir ---"
  ls -la "${FAILED_DIR}/" 2>/dev/null || true
}

echo "==> Wait for jobs to be seen (pending/ or approvals/ nonzero)"
wait_until 30 1 "queue sees jobs" \
  '[ "$(bucket_count "pending/")" -gt 0 ] || [ "$(bucket_count "pending/approvals/")" -gt 0 ]'

queue_status
./.venv/bin/voxera queue approvals list

# ---------------------------------------------------------------------------
# PHASE A: Wait until e2e-open reaches awaiting-approval state.
#
# Uses a direct filesystem check on the approval artifact path, which is
# deterministic and does not rely on CLI table parsing or path heuristics.
# The artifact is written by the daemon at a fixed location derived from the
# job filename — no guessing required.
# ---------------------------------------------------------------------------

PHASE_A_TIMEOUT=120

echo ""
echo "==> [PHASE A] Waiting for ${JOB_STEM} to reach awaiting-approval state..."

phase_a_start="$(date +%s)"
while true; do
  if approval_artifact_present; then
    echo ""
    echo "==> [PHASE A] ${JOB_STEM} is now awaiting approval"
    print_counts
    break
  fi

  now="$(date +%s)"
  elapsed=$((now - phase_a_start))

  if [ "${elapsed}" -ge "${PHASE_A_TIMEOUT}" ]; then
    echo ""
    echo "TIMEOUT [PHASE A]: ${JOB_STEM} did not reach approval state within ${PHASE_A_TIMEOUT}s"
    dump_diag
    exit 1
  fi

  # Print progress every 10 s so the terminal is not silent.
  if [ "${elapsed}" -gt 0 ] && [ $((elapsed % 10)) -eq 0 ]; then
    echo "  [PHASE A] still waiting... ${elapsed}s / ${PHASE_A_TIMEOUT}s"
    print_counts
  fi

  sleep 1
done

# ---------------------------------------------------------------------------
# Instruct operator to approve via the Panel.
# The script now enters PHASE B and waits for the job lifecycle to advance
# rather than checking for any specific approval artifact file.
# ---------------------------------------------------------------------------

echo ""
echo "################################################################"
echo "  APPROVAL REQUIRED"
echo "  Approve '${JOB_STEM}' via the Panel now:"
echo "  http://127.0.0.1:${PANEL_PORT}/"
echo "################################################################"
echo ""

# ---------------------------------------------------------------------------
# PHASE B: Wait for e2e-open to leave the approval gate and finish.
#
# Polls the filesystem for the job's terminal-state files (done/ or failed/).
# This is lifecycle-based: we do not care about intermediate approval file
# paths or how the panel writes its response — we only observe whether the
# job reached a terminal bucket.
# ---------------------------------------------------------------------------

PHASE_B_TIMEOUT=300

echo "==> [PHASE B] Waiting for ${JOB_STEM} to complete after approval (timeout ${PHASE_B_TIMEOUT}s)..."

phase_b_start="$(date +%s)"
while true; do
  if job_in_done; then
    echo ""
    echo "==> [PHASE B] ${JOB_STEM} reached done"
    break
  fi

  if job_in_failed; then
    echo ""
    echo "==> [PHASE B] ${JOB_STEM} reached failed — check diagnostics below"
    dump_diag
    exit 1
  fi

  now="$(date +%s)"
  elapsed=$((now - phase_b_start))

  if [ "${elapsed}" -ge "${PHASE_B_TIMEOUT}" ]; then
    echo ""
    echo "TIMEOUT [PHASE B]: ${JOB_STEM} did not finish within ${PHASE_B_TIMEOUT}s after approval"
    dump_diag
    exit 1
  fi

  # Print progress every 15 s.
  if [ "${elapsed}" -gt 0 ] && [ $((elapsed % 15)) -eq 0 ]; then
    echo "  [PHASE B] still waiting... ${elapsed}s / ${PHASE_B_TIMEOUT}s"
    print_counts
  fi

  sleep 1
done

queue_status

# ---------------------------------------------------------------------------
# Final settle: wait for all 4 jobs to reach done with no failures.
# ---------------------------------------------------------------------------

echo "==> Wait for all jobs to settle (expect done=4, failed=0, approvals=0, pending=0)"

SETTLE_TIMEOUT=120
settle_start="$(date +%s)"

while true; do
  print_counts
  d="$(bucket_count "done/")"
  f="$(bucket_count "failed/")"
  p="$(bucket_count "pending/")"
  a="$(bucket_count "pending/approvals/")"

  if [ "${d}" -eq 4 ] && [ "${f}" -eq 0 ] && [ "${p}" -eq 0 ] && [ "${a}" -eq 0 ]; then
    break
  fi

  now="$(date +%s)"
  elapsed=$((now - settle_start))

  if [ "${elapsed}" -ge "${SETTLE_TIMEOUT}" ]; then
    echo ""
    echo "TIMEOUT: jobs did not settle to expected state within ${SETTLE_TIMEOUT}s"
    echo "  expected: done=4 failed=0 pending=0 approvals=0"
    echo "  actual:   done=${d} failed=${f} pending=${p} approvals=${a}"
    dump_diag
    exit 1
  fi

  sleep 1
done

queue_status

echo "==> Verify notes write landed"
ls -la ~/VoxeraOS/notes | rg "ok\.txt" || true
cat ~/VoxeraOS/notes/ok.txt || true

echo "==> Verify sandbox argv normalization + HELLO-ARGV present"
./.venv/bin/voxera audit --n 600 | rg -n "HELLO-ARGV|args': \{'command': \['(bash|sh)', '-lc'" || true

# -------------------------------------------------------------------
# Hygiene + typing-ish checks (non-destructive)
# -------------------------------------------------------------------

echo "==> Hygiene: ruff format (CHECK ONLY)"
ruff format --check .

echo "==> Hygiene: ruff check"
ruff check .

echo "==> Hygiene: compileall (basic syntax sanity)"
python -m compileall -q src

echo "==> Hygiene: pytest"
pytest -q

echo "==> DONE"
