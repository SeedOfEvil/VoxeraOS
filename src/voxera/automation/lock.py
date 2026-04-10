"""Single-writer concurrency guard for the automation runner.

The automation runner evaluates saved definitions, decides which are due,
emits queue jobs via the inbox, writes history records, and updates
definition state.  Without a lock, two concurrent ``run-due-once``
invocations (e.g. from a systemd timer and a manual CLI call) can race
and double-submit queue jobs or corrupt definition state.

This module provides a lightweight POSIX filesystem lock
(``fcntl.flock``) scoped to the entire runner evaluation/submission
cycle.  The lock is:

- **Non-blocking**: if the lock is already held the caller gets an
  immediate failure, not a hang.
- **Distinct from the queue daemon lock**: the daemon uses
  ``<queue_root>/.daemon.lock``; the automation runner uses
  ``<queue_root>/automations/.runner.lock``.
- **Advisory**: the lock is cooperative.  It protects against accidental
  concurrent invocations, not against a caller that deliberately ignores
  it.

Usage::

    from voxera.automation.lock import acquire_runner_lock, release_runner_lock

    lock_result = acquire_runner_lock(queue_root)
    if not lock_result.acquired:
        # another runner is active — skip this pass
        ...
    try:
        # run evaluation / submission / history / state-update cycle
        ...
    finally:
        release_runner_lock(lock_result)
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .store import automations_root

RUNNER_LOCK_FILENAME = ".runner.lock"


@dataclass
class RunnerLockResult:
    """Outcome of a runner lock acquisition attempt."""

    acquired: bool
    lock_path: Path
    message: str
    _fd: int | None = None


def _runner_lock_path(queue_root: Path) -> Path:
    return automations_root(queue_root) / RUNNER_LOCK_FILENAME


def acquire_runner_lock(queue_root: Path) -> RunnerLockResult:
    """Try to acquire the automation runner lock (non-blocking).

    Returns a ``RunnerLockResult`` indicating whether the lock was
    obtained.  On success the caller **must** call
    ``release_runner_lock`` in a ``finally`` block when done.
    """
    lock_path = _runner_lock_path(queue_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return RunnerLockResult(
            acquired=False,
            lock_path=lock_path,
            message="automation runner lock is held — skipping this pass",
        )

    # Write a small payload so operators can inspect the lock file.
    payload = json.dumps({"pid": os.getpid(), "ts": time.time()})
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload.encode())

    return RunnerLockResult(
        acquired=True,
        lock_path=lock_path,
        message="automation runner lock acquired",
        _fd=fd,
    )


def release_runner_lock(result: RunnerLockResult) -> None:
    """Release the automation runner lock obtained by ``acquire_runner_lock``."""
    if result._fd is not None:
        try:
            fcntl.flock(result._fd, fcntl.LOCK_UN)
        finally:
            os.close(result._fd)
            result._fd = None


__all__ = [
    "RUNNER_LOCK_FILENAME",
    "RunnerLockResult",
    "acquire_runner_lock",
    "release_runner_lock",
]
