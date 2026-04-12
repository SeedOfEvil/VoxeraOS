from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from voxera import cli

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
TIMESTAMP_KEY_RE = re.compile(
    r"(?:^|_)(?:ts|timestamp|updated_at|generated_at|written_at)(?:_ms)?$"
)


QUEUE_HEALTH_CONFIG_VARIANT_KEYS = {"max_age_s", "max_count", "removed_jobs", "removed_sidecars"}


@dataclass(frozen=True)
class GoldenSurface:
    name: str
    args: tuple[str, ...]
    file_name: str
    renderer: str


SURFACES: tuple[GoldenSurface, ...] = (
    GoldenSurface("root-help", ("--help",), "voxera_help.txt", "help"),
    GoldenSurface("queue-help", ("queue", "--help"), "voxera_queue_help.txt", "help"),
    GoldenSurface("doctor-help", ("doctor", "--help"), "voxera_doctor_help.txt", "help"),
    GoldenSurface(
        "queue-status-help", ("queue", "status", "--help"), "voxera_queue_status_help.txt", "help"
    ),
    GoldenSurface(
        "queue-approvals-help",
        ("queue", "approvals", "--help"),
        "voxera_queue_approvals_help.txt",
        "help",
    ),
    GoldenSurface(
        "queue-reconcile-help",
        ("queue", "reconcile", "--help"),
        "voxera_queue_reconcile_help.txt",
        "help",
    ),
    GoldenSurface(
        "queue-prune-help", ("queue", "prune", "--help"), "voxera_queue_prune_help.txt", "help"
    ),
    GoldenSurface(
        "queue-health-help", ("queue", "health", "--help"), "voxera_queue_health_help.txt", "help"
    ),
    GoldenSurface(
        "queue-health-json",
        ("queue", "health", "--json"),
        "queue_health_empty.json",
        "queue_health_json",
    ),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _golden_dir() -> Path:
    return _repo_root() / "tests" / "golden"


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def normalize_help_text(text: str) -> str:
    stripped = _strip_ansi(text)
    lines = [line.rstrip() for line in stripped.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    non_empty = [line for line in lines if line]
    if non_empty and all(line.startswith(" ") for line in non_empty):
        lines = [line[1:] if line else line for line in lines]
    normalized = "\n".join(lines)
    normalized = normalized.replace("Usage: root", "Usage: voxera")
    normalized = normalized.replace(" root ", " voxera ")
    return normalized + "\n"


def _normalize_string_value(
    value: str, *, tmp_prefixes: tuple[str, ...], repo_root: str, home: str
) -> str:
    normalized = value
    for prefix in tmp_prefixes:
        if normalized.startswith(prefix):
            normalized = normalized.replace(prefix, "<TMP>", 1)
    if normalized.startswith(repo_root):
        normalized = normalized.replace(repo_root, "<REPO>", 1)
    if home and normalized.startswith(home):
        normalized = normalized.replace(home, "<HOME>", 1)
    return normalized


def normalize_json_payload(
    payload: Any, *, tmp_prefixes: tuple[str, ...], repo_root: str, home: str
) -> Any:
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in QUEUE_HEALTH_CONFIG_VARIANT_KEYS:
                out[key] = "<CONFIG_OR_RUNTIME_VALUE>"
            elif isinstance(value, (int, float)) and TIMESTAMP_KEY_RE.search(key):
                out[key] = "<TS_MS>"
            else:
                out[key] = normalize_json_payload(
                    value,
                    tmp_prefixes=tmp_prefixes,
                    repo_root=repo_root,
                    home=home,
                )
        return out
    if isinstance(payload, list):
        return [
            normalize_json_payload(item, tmp_prefixes=tmp_prefixes, repo_root=repo_root, home=home)
            for item in payload
        ]
    if isinstance(payload, str):
        return _normalize_string_value(
            payload,
            tmp_prefixes=tmp_prefixes,
            repo_root=repo_root,
            home=home,
        )
    return payload


def _render_surface(surface: GoldenSurface) -> str:
    import voxera.config as _config_mod

    runner = CliRunner()
    env = {"COLUMNS": "100", "VOXERA_LOAD_DOTENV": "0"}
    original_env = dict(os.environ)
    original_default_config_path = _config_mod.default_config_path
    try:
        for key in list(os.environ):
            if key.startswith("VOXERA_"):
                os.environ.pop(key, None)

        # Provide a stub config.yml so the first-run guard does not
        # block help-surface rendering when no real config is present.
        cfg_path = original_default_config_path()
        if not cfg_path.exists():
            stub_dir = Path(tempfile.mkdtemp())
            stub_cfg = stub_dir / "config.yml"
            stub_cfg.write_text("mode: cli\n", encoding="utf-8")
            _config_mod.default_config_path = lambda: stub_cfg

        if surface.renderer == "help":
            result = runner.invoke(cli.app, list(surface.args), color=False, env=env)
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to render {surface.name}: {result.exit_code}\n{result.stdout}\n{result.stderr}"
                )
            return normalize_help_text(result.stdout)

        if surface.renderer == "queue_health_json":
            with tempfile.TemporaryDirectory() as temp_dir:
                queue_dir = Path(temp_dir) / "queue"
                queue_dir.mkdir(parents=True, exist_ok=True)
                result = runner.invoke(
                    cli.app,
                    [*surface.args, "--queue-dir", str(queue_dir)],
                    color=False,
                    env=env,
                )
                if result.exit_code != 0:
                    raise RuntimeError(
                        f"Failed to render {surface.name}: {result.exit_code}\n{result.stdout}\n{result.stderr}"
                    )
                payload = json.loads(result.stdout)
                normalized = normalize_json_payload(
                    payload,
                    tmp_prefixes=(temp_dir,),
                    repo_root=str(_repo_root()),
                    home=str(Path.home()),
                )
                return json.dumps(normalized, indent=2, sort_keys=True) + "\n"

        raise ValueError(f"Unsupported renderer: {surface.renderer}")
    finally:
        _config_mod.default_config_path = original_default_config_path
        os.environ.clear()
        os.environ.update(original_env)


def update_golden_files() -> None:
    golden_dir = _golden_dir()
    golden_dir.mkdir(parents=True, exist_ok=True)
    for surface in SURFACES:
        output = _render_surface(surface)
        (golden_dir / surface.file_name).write_text(output, encoding="utf-8")


def check_golden_files() -> None:
    golden_dir = _golden_dir()
    failures: list[str] = []

    for surface in SURFACES:
        expected_path = golden_dir / surface.file_name
        if not expected_path.exists():
            failures.append(f"missing golden file: {expected_path}")
            continue
        expected = expected_path.read_text(encoding="utf-8")
        actual = _render_surface(surface)
        if expected != actual:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile=f"expected/{surface.file_name}",
                    tofile=f"actual/{surface.file_name}",
                    lineterm="",
                )
            )
            failures.append(f"drift detected for {surface.name}\n{diff}")

    if failures:
        raise SystemExit("\n\n".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage golden operator surface baselines.")
    parser.add_argument("--update", action="store_true", help="Regenerate committed golden files.")
    parser.add_argument(
        "--check", action="store_true", help="Fail when output drifts from committed goldens."
    )
    args = parser.parse_args()

    if args.update and args.check:
        raise SystemExit("pass either --update or --check, not both")

    if args.update:
        update_golden_files()
        return

    check_golden_files()


if __name__ == "__main__":
    main()
