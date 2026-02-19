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

echo "==> Wait for jobs to be seen (pending/ or approvals/ nonzero)"
wait_until 30 1 "queue sees jobs" \
  '[ "$(bucket_count "pending/")" -gt 0 ] || [ "$(bucket_count "pending/approvals/")" -gt 0 ]'

queue_status
./.venv/bin/voxera queue approvals list

echo "==> Wait until e2e-open hits approvals inbox"
wait_until 60 1 "e2e-open in approvals" \
  './.venv/bin/voxera queue approvals list | rg -q "job-e2e-open\.json|e2e-open"'

echo "==> Approve e2e-open"
./.venv/bin/voxera queue approvals approve job-e2e-open.json

echo "==> Wait for jobs to settle (expect done=4, failed=0, approvals=0, pending=0)"
for _ in $(seq 1 120); do
  print_counts
  d="$(bucket_count "done/")"
  f="$(bucket_count "failed/")"
  p="$(bucket_count "pending/")"
  a="$(bucket_count "pending/approvals/")"
  if [ "$d" -eq 4 ] && [ "$f" -eq 0 ] && [ "$p" -eq 0 ] && [ "$a" -eq 0 ]; then
    break
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
