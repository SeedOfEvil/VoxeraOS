#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/update.sh [OPTIONS]

Best-practice VoxeraOS update flow for local/service mode installs.

Options:
  --smoke         Run E2E dry-run smoke checks (E2E_DRY_RUN=1 make e2e)
  --restart       Restart installed/enabled user services after update
  --no-restart    Do not restart services
  --force         Continue when local git changes exist (uses rebase pull with autostash)
  --skip-tests    Skip compile/test checks
  --help, -h      Show this help message

Notes:
  - By default, updates require a clean working tree and branch "main".
  - Set VOXERA_UPDATE_ALLOW_BRANCH=1 to bypass branch enforcement.
  - This script does not delete ~/VoxeraOS/notes.
USAGE
}

run_smoke=0
restart_mode=auto
force_update=0
skip_tests=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke)
      run_smoke=1
      ;;
    --restart)
      restart_mode=on
      ;;
    --no-restart)
      restart_mode=off
      ;;
    --force)
      force_update=1
      ;;
    --skip-tests)
      skip_tests=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "Error: not inside a git repository. Run this from the VoxeraOS checkout." >&2
  exit 1
fi
cd "$repo_root"

echo "Repository root: $repo_root"
current_branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: $current_branch"

if [[ "${VOXERA_UPDATE_ALLOW_BRANCH:-0}" != "1" && "$current_branch" != "main" ]]; then
  echo "Error: updates are locked to branch 'main' by default." >&2
  echo "Set VOXERA_UPDATE_ALLOW_BRANCH=1 if you intentionally want to update this branch." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "$force_update" != "1" ]]; then
    echo "Error: local modifications detected; refusing to update." >&2
    echo "Hint: run 'git status' to inspect changes." >&2
    echo "Options: commit your work, stash it, or re-run with --force." >&2
    exit 1
  fi
  echo "Warning: local modifications detected; proceeding due to --force." >&2
  echo "A rebase pull with autostash will be used." >&2
fi

echo "Fetching latest changes..."
git fetch --all --prune

echo "Pulling latest commits..."
if [[ "$force_update" == "1" ]]; then
  git pull --rebase --autostash
else
  git pull --ff-only
fi

project_dir="$repo_root"
cd "$project_dir"

if [[ ! -d .venv ]]; then
  echo "Virtual environment not found; creating .venv..."
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
  else
    echo "python3.12 not found, falling back to python3"
    python3 -m venv .venv
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install -U pip
pip install -e ".[dev]"

if [[ "$skip_tests" != "1" ]]; then
  echo "Running compile and test checks..."
  python -m compileall src
  pytest -q
else
  echo "Skipping compile/test checks (--skip-tests)."
fi

if [[ "$run_smoke" == "1" ]]; then
  echo "Running optional smoke checks..."
  E2E_DRY_RUN=1 make e2e
fi

restart_requested=0
if [[ "$restart_mode" == "on" ]]; then
  restart_requested=1
elif [[ "$restart_mode" == "auto" ]]; then
  if systemctl --user list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -Eq '^(voxera-daemon|voxera-panel)\.service$'; then
    restart_requested=1
  fi
fi

if [[ "$restart_requested" == "1" ]]; then
  echo "Reloading and restarting user services (if enabled)..."
  systemctl --user daemon-reload
  for unit in voxera-daemon.service voxera-panel.service; do
    if systemctl --user list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$unit"; then
      if systemctl --user is-enabled "$unit" >/dev/null 2>&1; then
        echo "Restarting $unit"
        systemctl --user restart "$unit"
      else
        echo "Skipping $unit (installed but not enabled)."
      fi
    else
      echo "Skipping $unit (not installed)."
    fi
  done

  systemctl --user --no-pager status voxera-daemon.service voxera-panel.service || true
else
  echo "Service restart skipped."
fi

echo
echo "Update complete."
echo "Verify with:"
echo "  voxera status"
echo "  voxera queue status"
