from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import AppConfig, RunResult, SkillManifest
from .arg_normalizer import canonicalize_argv

DEFAULT_ENV_ALLOWLIST = {
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
}

SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|SECRET|PASS|PASSWORD|API|AUTH)", re.IGNORECASE)
LONG_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")
HEX_SECRET_VALUE_RE = re.compile(r"^[A-Fa-f0-9]{24,}$")
LONG_SECRET_FRAGMENT_RE = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
HEX_SECRET_FRAGMENT_RE = re.compile(r"[A-Fa-f0-9]{24,}")


@dataclass(frozen=True)
class JobPaths:
    job_id: str
    workspace_dir: Path
    artifacts_dir: Path


def _voxera_root() -> Path:
    return Path.home() / ".voxera"


def ensure_job_paths(job_id: str) -> JobPaths:
    root = _voxera_root()
    workspace = root / "workspace" / job_id
    artifacts = root / "artifacts" / job_id
    cache = root / "cache"
    workspace.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    return JobPaths(job_id=job_id, workspace_dir=workspace, artifacts_dir=artifacts)


def generate_job_id() -> str:
    return uuid.uuid4().hex


def _looks_secret_value(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    return bool(
        LONG_SECRET_VALUE_RE.match(stripped)
        or HEX_SECRET_VALUE_RE.match(stripped)
        or LONG_SECRET_FRAGMENT_RE.search(stripped)
        or HEX_SECRET_FRAGMENT_RE.search(stripped)
    )


def redact_value(key: str, value: str) -> str:
    if SECRET_KEY_RE.search(key) or _looks_secret_value(value):
        return "REDACTED"
    return value


def sanitize_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: redact_value(k, v) for k, v in env.items()}


def sanitize_command(command: Iterable[str]) -> list[str]:
    redacted: list[str] = []
    for arg in command:
        if _looks_secret_value(arg):
            redacted.append("REDACTED")
        else:
            redacted.append(arg)
    return redacted


def _text_from_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def sanitize_audit_value(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        return {k: sanitize_audit_value(v, key_hint=k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_audit_value(item, key_hint=key_hint) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit_value(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return redact_value(key_hint, value)
    return value


class ExecutionRunner(ABC):
    runner_name = "base"

    @abstractmethod
    def run(
        self,
        *,
        manifest: SkillManifest,
        args: dict[str, Any],
        fn: Callable[..., Any],
        cfg: AppConfig,
        job_id: str,
    ) -> RunResult:
        raise NotImplementedError


class LocalRunner(ExecutionRunner):
    runner_name = "local"

    def run(
        self,
        *,
        manifest: SkillManifest,
        args: dict[str, Any],
        fn: Callable[..., Any],
        cfg: AppConfig,
        job_id: str,
    ) -> RunResult:
        out = fn(**args)
        rr = out if isinstance(out, RunResult) else RunResult(ok=True, output=str(out))
        rr.data.setdefault("runner", self.runner_name)
        rr.data.setdefault("job_id", job_id)
        return rr


class SandboxRunner(ExecutionRunner, ABC):
    runner_name = "sandbox"


class PodmanSandboxRunner(SandboxRunner):
    runner_name = "sandbox.podman"

    @staticmethod
    def _parse_network_setting(raw_value: Any) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value is None:
            return False
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
            raise ValueError("network must be a boolean value")
        raise ValueError("network must be a boolean value")

    def _assert_available(self) -> None:
        if shutil.which("podman") is None:
            raise RuntimeError(
                "Podman is required for sandbox execution. Install rootless Podman and retry (see README)."
            )

    def run(
        self,
        *,
        manifest: SkillManifest,
        args: dict[str, Any],
        fn: Callable[..., Any],
        cfg: AppConfig,
        job_id: str,
    ) -> RunResult:
        self._assert_available()
        try:
            command = canonicalize_argv(args)
        except ValueError as exc:
            return RunResult(ok=False, error=str(exc))
        args = {**args, "command": command}

        timeout_s = int(args.get("timeout_s", 60))
        if timeout_s <= 0:
            return RunResult(ok=False, error="timeout_s must be greater than zero")

        try:
            requested_network = self._parse_network_setting(args.get("network", False))
        except ValueError as exc:
            return RunResult(ok=False, error=str(exc))
        env_arg = args.get("env", {}) or {}
        if not isinstance(env_arg, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env_arg.items()
        ):
            return RunResult(ok=False, error="env must be a dict of string keys and values")

        safe_env = {k: v for k, v in env_arg.items() if k in DEFAULT_ENV_ALLOWLIST}
        paths = ensure_job_paths(job_id)

        stdout_path = paths.artifacts_dir / "stdout.txt"
        stderr_path = paths.artifacts_dir / "stderr.txt"
        runner_path = paths.artifacts_dir / "runner.json"
        command_path = paths.artifacts_dir / "command.txt"

        start_ts = time.time()
        volume = f"{paths.workspace_dir}:/work:rw,Z"
        podman_cmd: list[str] = [
            "podman",
            "run",
            "--rm",
            "--read-only",
            "--workdir",
            "/work",
            "-v",
            volume,
            "--memory",
            str(cfg.sandbox_memory),
            "--cpus",
            str(cfg.sandbox_cpus),
            "--pids-limit",
            str(cfg.sandbox_pids_limit),
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "-e",
            "HOME=/work",
        ]
        for k, v in safe_env.items():
            podman_cmd.extend(["-e", f"{k}={v}"])

        podman_cmd.extend(
            ["--network", "bridge" if requested_network else "none", cfg.sandbox_image]
        )
        podman_cmd.extend(command)

        sanitized_podman = " ".join(shlex.quote(arg) for arg in sanitize_command(podman_cmd))
        command_path.write_text(sanitized_podman, encoding="utf-8")

        try:
            proc = subprocess.run(
                podman_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = _text_from_subprocess_output(exc.stdout)
            stderr = _text_from_subprocess_output(exc.stderr) + "\nTimed out"
            exit_code = 124
            timed_out = True

        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        end_ts = time.time()
        runner_metadata = {
            "runner": self.runner_name,
            "skill": manifest.id,
            "image": cfg.sandbox_image,
            "job_id": job_id,
            "network": requested_network,
            "limits": {
                "memory": cfg.sandbox_memory,
                "cpus": cfg.sandbox_cpus,
                "pids_limit": cfg.sandbox_pids_limit,
                "timeout_s": timeout_s,
            },
            "env": sanitize_env(safe_env),
            "started_at": start_ts,
            "ended_at": end_ts,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "artifacts_dir": str(paths.artifacts_dir),
            "workspace_dir": str(paths.workspace_dir),
        }
        runner_path.write_text(json.dumps(runner_metadata, indent=2), encoding="utf-8")

        return RunResult(
            ok=(exit_code == 0 and not timed_out),
            output=stdout.strip(),
            error=None
            if exit_code == 0 and not timed_out
            else (
                "Sandbox command timed out"
                if timed_out
                else stderr.strip() or f"Sandbox command failed with exit code {exit_code}"
            ),
            data={
                "runner": self.runner_name,
                "job_id": job_id,
                "exit_code": exit_code,
                "artifacts_dir": str(paths.artifacts_dir),
                "workspace_dir": str(paths.workspace_dir),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "runner_path": str(runner_path),
                "command_path": str(command_path),
                "network": requested_network,
            },
        )


def select_runner(manifest: SkillManifest) -> ExecutionRunner:
    if manifest.exec_mode == "sandbox":
        return PodmanSandboxRunner()
    return LocalRunner()
