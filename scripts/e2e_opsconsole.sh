#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$ROOT_DIR/notes/queue}"

print_archive_diag() {
  echo ":: diagnostics: archive tree"
  ls -lah "$QUEUE_ROOT/_archive" || true
  find "$QUEUE_ROOT/_archive" -maxdepth 2 -type f | sort || true
}

require_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "$path" ]]; then
    echo ":: error: missing $label: $path"
    print_archive_diag
    exit 1
  fi
}

echo ":: step: ensure queue directories"
mkdir -p "$QUEUE_ROOT"/{inbox,pending/approvals,done,failed,artifacts,_archive}

echo ":: step: restart daemon service"
systemctl --user restart voxera-daemon.service

JOB_FILE="job-e2e-ops-$(date +%s).json"
JOB_STEM="${JOB_FILE%.json}"

echo ":: step: enqueue approval-required inbox job ($JOB_FILE)"
cat > "$QUEUE_ROOT/inbox/$JOB_FILE" <<'JSON'
{
  "goal": "Open example.com for ops e2e",
  "steps": [
    {"skill_id": "system.open_url", "args": {"url": "https://example.com"}}
  ]
}
JSON

APPROVAL_FILE="$QUEUE_ROOT/pending/approvals/${JOB_STEM}.approval.json"
echo ":: step: wait for approval artifact ($APPROVAL_FILE)"
for _ in $(seq 1 120); do
  [[ -f "$APPROVAL_FILE" ]] && break
  sleep 1
done
require_file "$APPROVAL_FILE" "approval artifact"

echo ":: step: approve queued job"
voxera queue approvals approve "$JOB_FILE"

DONE_FILE="$QUEUE_ROOT/done/$JOB_FILE"
echo ":: step: wait for done file ($DONE_FILE)"
for _ in $(seq 1 180); do
  [[ -f "$DONE_FILE" ]] && break
  sleep 1
done
require_file "$DONE_FILE" "done file"

echo ":: step: validate expected artifacts"
ART_DIR="$QUEUE_ROOT/artifacts/$JOB_STEM"
require_file "$ART_DIR/plan.json" "plan artifact"
require_file "$ART_DIR/actions.jsonl" "actions artifact"
require_file "$ART_DIR/stdout.txt" "stdout artifact"
require_file "$ART_DIR/stderr.txt" "stderr artifact"

echo ":: step: run doctor self-test"
voxera doctor --self-test | tee /tmp/voxera-doctor-self-test.out
rg -q "PASS" /tmp/voxera-doctor-self-test.out

echo ":: step: export system ops bundle"
system_zip="$(voxera ops bundle system | tail -n1)"
require_file "$system_zip" "system bundle"
archive_dir="$(dirname "$system_zip")"
echo ":: archive_dir=$archive_dir"

echo ":: step: export job ops bundle"
job_zip="$(voxera ops bundle job "$JOB_FILE" | tail -n1)"
require_file "$job_zip" "job bundle"

echo ":: step: validate bundle manifests"
if command -v unzip >/dev/null 2>&1; then
  unzip -l "$system_zip" | rg -q "manifest.json"
  unzip -l "$job_zip" | rg -q "manifest.json"
else
  python -m zipfile -l "$system_zip" | rg -q "manifest.json"
  python -m zipfile -l "$job_zip" | rg -q "manifest.json"
fi

echo "E2E OK"
