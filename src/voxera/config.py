from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import AppConfig
from .paths import config_dir, data_dir, ensure_dirs
from .paths import queue_root as default_queue_root

DEFAULT_CONFIG_NAME = "config.yml"
DEFAULT_POLICY_NAME = "policy.yml"
_DEFAULT_ENV_FILE = Path("~/.config/voxera/env").expanduser()


@dataclass(frozen=True)
class VoxeraSettings:
    queue_root: Path
    panel_host: str
    panel_port: int
    panel_operator_user: str
    panel_operator_password: str | None
    panel_enable_get_mutations: bool
    queue_lock_stale_s: float
    queue_failed_max_age_s: float | None
    queue_failed_max_count: int | None
    ops_bundle_dir: Path | None
    dev_mode: bool
    notify_enabled: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> VoxeraSettings:
        env = dict(environ or os.environ)
        _ = cwd
        _ = home

        queue_root = _queue_root_default()
        queue_root_raw = (env.get("VOXERA_QUEUE_ROOT") or "").strip()
        if queue_root_raw:
            queue_root = Path(queue_root_raw).expanduser()

        panel_operator_password = (env.get("VOXERA_PANEL_OPERATOR_PASSWORD") or "").strip() or None

        ops_bundle_raw = (env.get("VOXERA_OPS_BUNDLE_DIR") or "").strip()
        ops_bundle_dir = Path(ops_bundle_raw).expanduser() if ops_bundle_raw else None

        return cls(
            queue_root=queue_root,
            panel_host=(env.get("VOXERA_PANEL_HOST") or "127.0.0.1").strip() or "127.0.0.1",
            panel_port=_parse_int(env, "VOXERA_PANEL_PORT", default=8844, min_value=1),
            panel_operator_user=(env.get("VOXERA_PANEL_OPERATOR_USER") or "admin").strip()
            or "admin",
            panel_operator_password=panel_operator_password,
            panel_enable_get_mutations=_parse_bool(
                env, "VOXERA_PANEL_ENABLE_GET_MUTATIONS", default=False
            ),
            queue_lock_stale_s=_parse_float(
                env, "VOXERA_QUEUE_LOCK_STALE_S", default=3600.0, min_value=0.0
            ),
            queue_failed_max_age_s=_parse_optional_float(
                env, "VOXERA_QUEUE_FAILED_MAX_AGE_S", min_value=0.0
            ),
            queue_failed_max_count=_parse_optional_int(
                env, "VOXERA_QUEUE_FAILED_MAX_COUNT", min_value=1
            ),
            ops_bundle_dir=ops_bundle_dir,
            dev_mode=_parse_bool(env, "VOXERA_DEV_MODE", default=False),
            notify_enabled=_parse_bool(env, "VOXERA_NOTIFY", default=False),
        )

    def to_safe_dict(self) -> dict[str, str | int | float | bool | None]:
        safe: dict[str, str | int | float | bool | None] = {
            "queue_root": str(self.queue_root),
            "panel_host": self.panel_host,
            "panel_port": self.panel_port,
            "panel_operator_user": self.panel_operator_user,
            "panel_operator_password": self.panel_operator_password,
            "panel_enable_get_mutations": self.panel_enable_get_mutations,
            "queue_lock_stale_s": self.queue_lock_stale_s,
            "queue_failed_max_age_s": self.queue_failed_max_age_s,
            "queue_failed_max_count": self.queue_failed_max_count,
            "ops_bundle_dir": str(self.ops_bundle_dir) if self.ops_bundle_dir else None,
            "dev_mode": self.dev_mode,
            "notify_enabled": self.notify_enabled,
        }
        for key, value in list(safe.items()):
            if value is None:
                continue
            lowered = key.lower()
            if any(token in lowered for token in ("password", "token", "key", "secret")):
                safe[key] = "***"
        return safe


def _queue_root_default() -> Path:
    return default_queue_root()


def _parse_bool(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid value for {key}: expected boolean, got {raw!r}")


def _parse_int(
    env: Mapping[str, str], key: str, *, default: int, min_value: int | None = None
) -> int:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {key}: expected int, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {value}")
    return value


def _parse_optional_int(
    env: Mapping[str, str], key: str, *, min_value: int | None = None
) -> int | None:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {key}: expected int, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {value}")
    return value


def _parse_float(
    env: Mapping[str, str], key: str, *, default: float, min_value: float | None = None
) -> float:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {key}: expected float, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {value}")
    return value


def _parse_optional_float(
    env: Mapping[str, str], key: str, *, min_value: float | None = None
) -> float | None:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {key}: expected float, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {value}")
    return value


def load_env_file(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    if not path.exists():
        return payload
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_no} in {path}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env line {line_no} in {path}: empty key")
        payload[key] = value.strip()
    return payload


def load_runtime_env(path: Path | None = None) -> dict[str, str]:
    env_path = path or _DEFAULT_ENV_FILE
    loaded = load_env_file(env_path)
    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    return loaded


def default_config_path():
    return config_dir() / DEFAULT_CONFIG_NAME


def load_config(path=None) -> AppConfig:
    ensure_dirs()
    path = path or default_config_path()
    if not path.exists():
        return AppConfig()
    obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(obj)


def save_config(cfg: AppConfig, path=None) -> None:
    ensure_dirs()
    path = path or default_config_path()
    path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False), encoding="utf-8")


def policy_path():
    ensure_dirs()
    return config_dir() / DEFAULT_POLICY_NAME


def load_policy(path=None):
    ensure_dirs()
    path = path or policy_path()
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def save_policy(policy: dict, path=None):
    ensure_dirs()
    path = path or policy_path()
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")


def capabilities_report_path():
    ensure_dirs()
    return data_dir() / "capabilities.json"
