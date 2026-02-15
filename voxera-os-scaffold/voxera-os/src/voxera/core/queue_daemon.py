from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit import log
from ..config import load_config
from ..skills.registry import SkillRegistry
from ..skills.runner import SkillRunner
from .mission_planner import MissionPlannerError, plan_mission
from .missions import MissionRunner, get_mission


@dataclass
class QueueStats:
    processed: int = 0
    failed: int = 0


class MissionQueueDaemon:
    def __init__(
        self,
        queue_root: Path | None = None,
        poll_interval: float = 1.0,
        mission_log_path: Path | None = None,
    ):
        self.queue_root = (queue_root or (Path.home() / "VoxeraOS" / "notes" / "queue")).expanduser()
        self.inbox = self.queue_root
        self.done = self.queue_root / "done"
        self.failed = self.queue_root / "failed"
        self.poll_interval = poll_interval
        self.stats = QueueStats()

        cfg = load_config()
        reg = SkillRegistry()
        reg.discover()
        runner = SkillRunner(reg)
        self.mission_runner = MissionRunner(
            runner,
            policy=cfg.policy,
            redact_logs=cfg.privacy.redact_logs,
            mission_log_path=mission_log_path,
        )
        self.cfg = cfg

    def ensure_dirs(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.done.mkdir(parents=True, exist_ok=True)
        self.failed.mkdir(parents=True, exist_ok=True)

    def _move_job(self, src: Path, target_dir: Path) -> Path:
        target = target_dir / src.name
        if target.exists():
            ts = int(time.time() * 1000)
            target = target_dir / f"{src.stem}-{ts}{src.suffix}"
        shutil.move(str(src), str(target))
        return target

    def process_job_file(self, job_path: Path) -> bool:
        self.ensure_dirs()
        log({"event": "queue_job_received", "job": str(job_path)})
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("job payload must be a JSON object")
        except Exception as exc:
            moved = self._move_job(job_path, self.failed)
            self.stats.failed += 1
            log({"event": "queue_job_failed", "job": str(moved), "error": repr(exc)})
            return False

        try:
            rr = self._execute_payload(payload)
            if not rr.ok:
                raise RuntimeError(rr.error or "mission failed")
        except Exception as exc:
            moved = self._move_job(job_path, self.failed)
            self.stats.failed += 1
            log({"event": "queue_job_failed", "job": str(moved), "error": repr(exc)})
            return False

        moved = self._move_job(job_path, self.done)
        self.stats.processed += 1
        log({"event": "queue_job_done", "job": str(moved)})
        return True

    def _execute_payload(self, payload: dict[str, Any]):
        if "mission_id" in payload:
            mission = get_mission(str(payload["mission_id"]))
            log({"event": "queue_job_started", "kind": "mission_id", "mission": mission.id})
            return self.mission_runner.run(mission)
        if "goal" in payload:
            goal = str(payload["goal"])
            log({"event": "queue_job_started", "kind": "goal", "goal": goal})
            try:
                mission = asyncio.run(plan_mission(goal=goal, cfg=self.cfg, registry=self.mission_runner.skill_runner.registry))
            except MissionPlannerError as exc:
                raise RuntimeError(str(exc)) from exc
            return self.mission_runner.run(mission)
        raise ValueError("job must contain either mission_id or goal")

    def process_pending_once(self) -> int:
        self.ensure_dirs()
        processed = 0
        for job in sorted(self.inbox.glob("*.json")):
            if job.parent != self.inbox:
                continue
            self.process_job_file(job)
            processed += 1
        return processed

    def run(self, once: bool = False) -> None:
        self.ensure_dirs()
        log({"event": "queue_daemon_start", "queue": str(self.inbox), "once": once})
        if once:
            count = self.process_pending_once()
            log({"event": "queue_daemon_stop", "reason": "once", "processed": count})
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            daemon = self

            class _Handler(FileSystemEventHandler):
                def on_created(self, event):
                    if event.is_directory:
                        return
                    path = Path(event.src_path)
                    if path.suffix.lower() == ".json" and path.parent == daemon.inbox:
                        daemon.process_job_file(path)

            observer = Observer()
            observer.schedule(_Handler(), str(self.inbox), recursive=False)
            observer.start()
            log({"event": "queue_watch_mode", "mode": "watchdog"})
            try:
                while True:
                    time.sleep(self.poll_interval)
            finally:
                observer.stop()
                observer.join()
        except ImportError:
            log({"event": "queue_watch_mode", "mode": "poll"})
            while True:
                self.process_pending_once()
                time.sleep(self.poll_interval)
