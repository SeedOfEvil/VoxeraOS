#!/usr/bin/env python3
"""Fail when mypy reports errors not present in the committed baseline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_CMD = ["mypy", "src/voxera", "--ignore-missing-imports"]


def parse_errors(stdout: str) -> set[str]:
    errors: set[str] = set()
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("Found ", "Success: ")):
            continue
        if ": error:" not in stripped:
            continue
        errors.add(stripped)
    return errors


def read_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def run_mypy(command: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="tools/mypy-baseline.txt")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Overwrite baseline with current mypy errors and exit 0.",
    )
    parser.add_argument(
        "--mypy-cmd",
        nargs=argparse.REMAINDER,
        help="Optional mypy command override (use after --mypy-cmd).",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    command = args.mypy_cmd if args.mypy_cmd else DEFAULT_CMD

    rc, stdout, stderr = run_mypy(command)
    current_errors = parse_errors(stdout)

    if args.write_baseline:
        if rc not in (0, 1):
            print(
                f"mypy failed with unexpected exit code {rc}; refusing to write baseline.",
                file=sys.stderr,
            )
            if stdout:
                print(stdout, end="")
            if stderr:
                print(stderr, end="", file=sys.stderr)
            return rc

        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            "\n".join(sorted(current_errors)) + ("\n" if current_errors else ""),
            encoding="utf-8",
        )
        print(
            f"Wrote {len(current_errors)} baseline mypy error(s) to {baseline_path}.",
            file=sys.stderr,
        )
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)
        return 0

    baseline_errors = read_baseline(baseline_path)
    new_errors = sorted(current_errors - baseline_errors)
    resolved_baseline_errors = sorted(baseline_errors - current_errors)

    if new_errors:
        print("mypy ratchet failed: new errors detected:", file=sys.stderr)
        for err in new_errors:
            print(f"  + {err}", file=sys.stderr)
        if resolved_baseline_errors:
            print(
                "\nNote: some baseline errors were resolved; run `make update-mypy-baseline` to refresh.",
                file=sys.stderr,
            )
        if stdout:
            print("\nRaw mypy output:", file=sys.stderr)
            print(stdout, end="", file=sys.stderr)
        if stderr:
            print(stderr, end="", file=sys.stderr)
        return 1

    print(
        f"mypy ratchet passed: {len(current_errors)} current error(s), no regressions against baseline ({baseline_path})."
    )
    if rc not in (0, 1):
        print(stderr, end="", file=sys.stderr)
        return rc

    if resolved_baseline_errors:
        print(
            "Baseline contains resolved errors; run `make update-mypy-baseline` to keep it current.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
