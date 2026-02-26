#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

QUEUE_ROOT="${VOXERA_QUEUE_ROOT:-$ROOT_DIR/notes/queue}"
mkdir -p "$QUEUE_ROOT"/{inbox,pending/approvals,done,failed,artifacts,_archive}

systemctl --user restart voxera-daemon.service

JOB_FILE="job-e2e-ops-$(date +%s).json"
JOB_STEM="${JOB_FILE%.json}"
cat > "$QUEUE_ROOT/inbox/$JOB_FILE" <<'JSON'
{
  "goal": "Open example.com for ops e2e",
  "steps": [
    {"skill_id": "system.open_url", "args": {"url": "https://example.com"}}
  ]
}
JSON

APPROVAL_FILE="$QUEUE_ROOT/pending/approvals/${JOB_STEM}.approval.json"
for _ in $(seq 1 120); do
  [[ -f "$APPROVAL_FILE" ]] && break
  sleep 1
done
[[ -f "$APPROVAL_FILE" ]]

voxera queue approvals approve "$JOB_FILE"

DONE_FILE="$QUEUE_ROOT/done/$JOB_FILE"
for _ in $(seq 1 180); do
  [[ -f "$DONE_FILE" ]] && break
  sleep 1
done
[[ -f "$DONE_FILE" ]]

ART_DIR="$QUEUE_ROOT/artifacts/$JOB_STEM"
[[ -f "$ART_DIR/plan.json" ]]
[[ -f "$ART_DIR/actions.jsonl" ]]
[[ -f "$ART_DIR/stdout.txt" ]]
[[ -f "$ART_DIR/stderr.txt" ]]

voxera doctor --self-test | tee /tmp/voxera-doctor-self-test.out
rg -q "PASS" /tmp/voxera-doctor-self-test.out

SYS_BUNDLE_PATH="$(voxera ops bundle system | tail -n1)"
JOB_BUNDLE_PATH="$(voxera ops bundle job "$JOB_FILE" | tail -n1)"

[[ -f "$SYS_BUNDLE_PATH" ]]
[[ -f "$JOB_BUNDLE_PATH" ]]

if command -v unzip >/dev/null 2>&1; then
  unzip -l "$SYS_BUNDLE_PATH" | rg -q "manifest.json"
  unzip -l "$JOB_BUNDLE_PATH" | rg -q "manifest.json"
fi

echo "E2E OK"
