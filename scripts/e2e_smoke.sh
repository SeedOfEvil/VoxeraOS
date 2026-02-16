#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

print_dep_hint() {
  local dep="$1"
  local hint="$2"
  if ! command -v "$dep" >/dev/null 2>&1; then
    echo "[warn] Missing OS dependency: $dep"
    echo "       Install hint: $hint"
  fi
}

echo "[e2e] checking optional OS dependencies"
print_dep_hint "wmctrl" "sudo apt-get install wmctrl"
print_dep_hint "xdg-open" "sudo apt-get install xdg-utils"
print_dep_hint "xclip" "sudo apt-get install xclip (clipboard)"
print_dep_hint "xsel" "sudo apt-get install xsel (clipboard)"
print_dep_hint "wl-copy" "sudo apt-get install wl-clipboard (clipboard)"
print_dep_hint "pactl" "sudo apt-get install pulseaudio-utils"

if [[ "${E2E_DRY_RUN:-0}" == "1" ]]; then
  echo "[e2e] dry mode enabled; skipping command execution"
  exit 0
fi

echo "[e2e] pytest"
pytest -q

echo "[e2e] compileall"
python -m compileall src

echo "[e2e] cli smoke (dry-run)"
voxera status >/dev/null
voxera skills list >/dev/null
voxera missions list >/dev/null
voxera missions run system_check --dry-run >/dev/null
voxera run system.open_app --arg name=firefox --dry-run >/dev/null

if [[ "${E2E_RUN_LIVE:-0}" == "1" ]]; then
  echo "[e2e] running live daily_checkin mission"
  voxera missions run daily_checkin
else
  echo "[e2e] skipping live daily_checkin mission (set E2E_RUN_LIVE=1 to enable)"
fi

echo "[e2e] done"
