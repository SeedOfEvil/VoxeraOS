from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from pathlib import Path


def move_job_with_sidecar(
    src: Path,
    target_dir: Path,
    *,
    on_already_moved: Callable[[Path, Path], None] | None = None,
) -> Path | None:
    target = target_dir / src.name
    if target.exists():
        ts = int(time.time() * 1000)
        target = target_dir / f"{src.stem}-{ts}{src.suffix}"
    try:
        src.replace(target)
    except FileNotFoundError:
        if on_already_moved is not None:
            on_already_moved(src, target_dir)
        return None

    state_src = src.with_name(f"{src.stem}.state.json")
    state_dst = target.with_name(f"{target.stem}.state.json")
    if state_src.exists():
        with contextlib.suppress(FileNotFoundError):
            state_src.replace(state_dst)
    return target


def deterministic_target_path(
    target_dir: Path,
    file_name: str,
    *,
    suffix_tag: str,
) -> Path:
    base = Path(file_name)
    candidate = target_dir / base.name
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        indexed = target_dir / f"{base.stem}-{suffix_tag}-{index}{base.suffix}"
        if not indexed.exists():
            return indexed
        index += 1
