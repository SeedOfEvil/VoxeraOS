from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import AppConfig
from .paths import config_dir, data_dir, ensure_dirs
from .paths import queue_root as default_queue_root

DEFAULT_CONFIG_NAME = "config.yml"
DEFAULT_POLICY_NAME = "policy.yml"
_DEFAULT_ENV_FILE = Path("~/.config/voxera/env").expanduser()
_DEFAULT_RUNTIME_CONFIG = Path("~/.config/voxera/config.json").expanduser()

# Single source of truth for the canonical Vera web app base URL.
# Referenced here (defaults dict + coerce fallback) and by
# ``voxera.panel.routes_voice`` for the continue-in-Vera link builder.
DEFAULT_VERA_WEB_BASE_URL = "http://127.0.0.1:8790"


@dataclass(frozen=True)
class VoxeraConfig:
    queue_root: Path
    panel_host: str
    panel_port: int
    panel_operator_user: str
    panel_operator_password: str | None
    panel_csrf_enabled: bool
    panel_enable_get_mutations: bool
    queue_lock_stale_s: float
    queue_failed_max_age_s: float | None
    queue_failed_max_count: int | None
    artifacts_retention_days: int | None
    artifacts_retention_max_count: int | None
    queue_prune_max_age_days: int | None
    queue_prune_max_count: int | None
    ops_bundle_dir: Path | None
    dev_mode: bool
    notify_enabled: bool
    # Absolute base URL of the canonical Vera web app (``vera_web``), used
    # by cross-surface continuation links (e.g. the Voice Workbench
    # "Continue in Vera" link).  Panel (8844) and vera_web (8790) run as
    # separate uvicorn processes by default, so a relative ``/vera`` link
    # 404s on the panel host — links must be built against this base URL.
    vera_web_base_url: str
    config_path: Path
    sources: Mapping[str, str]

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "queue_root": str(self.queue_root),
            "panel_host": self.panel_host,
            "panel_port": self.panel_port,
            "panel_operator_user": self.panel_operator_user,
            "panel_operator_password": self.panel_operator_password,
            "panel_csrf_enabled": self.panel_csrf_enabled,
            "panel_enable_get_mutations": self.panel_enable_get_mutations,
            "queue_lock_stale_s": self.queue_lock_stale_s,
            "queue_failed_max_age_s": self.queue_failed_max_age_s,
            "queue_failed_max_count": self.queue_failed_max_count,
            "artifacts_retention_days": self.artifacts_retention_days,
            "artifacts_retention_max_count": self.artifacts_retention_max_count,
            "queue_prune_max_age_days": self.queue_prune_max_age_days,
            "queue_prune_max_count": self.queue_prune_max_count,
            "ops_bundle_dir": str(self.ops_bundle_dir) if self.ops_bundle_dir else None,
            "dev_mode": self.dev_mode,
            "notify_enabled": self.notify_enabled,
            "vera_web_base_url": self.vera_web_base_url,
            "config_path": str(self.config_path),
            "sources": dict(self.sources),
        }
        for key in list(payload.keys()):
            lowered = key.lower()
            if (
                any(token in lowered for token in ("password", "token", "key", "secret"))
                and payload[key] is not None
            ):
                payload[key] = "***"
        return payload


def resolve_config_path(config_path: Path | None = None) -> Path:
    return (config_path or _DEFAULT_RUNTIME_CONFIG).expanduser()


def load_config(
    *, overrides: Mapping[str, Any] | None = None, config_path: Path | None = None
) -> VoxeraConfig:
    path = resolve_config_path(config_path)
    file_values = _load_runtime_config_file(path)
    env = dict(os.environ)
    override_values = dict(overrides or {})

    defaults: dict[str, Any] = {
        "queue_root": _queue_root_default(Path.cwd()),
        "panel_host": "127.0.0.1",
        "panel_port": 8844,
        "panel_operator_user": "admin",
        "panel_operator_password": None,
        "panel_csrf_enabled": True,
        "panel_enable_get_mutations": False,
        "queue_lock_stale_s": 3600.0,
        "queue_failed_max_age_s": None,
        "queue_failed_max_count": None,
        "artifacts_retention_days": None,
        "artifacts_retention_max_count": None,
        "queue_prune_max_age_days": None,
        "queue_prune_max_count": None,
        "ops_bundle_dir": None,
        "dev_mode": False,
        "notify_enabled": False,
        "vera_web_base_url": DEFAULT_VERA_WEB_BASE_URL,
    }

    env_map: dict[str, str] = {
        "queue_root": "VOXERA_QUEUE_ROOT",
        "panel_host": "VOXERA_PANEL_HOST",
        "panel_port": "VOXERA_PANEL_PORT",
        "panel_operator_user": "VOXERA_PANEL_OPERATOR_USER",
        "panel_operator_password": "VOXERA_PANEL_OPERATOR_PASSWORD",
        "panel_csrf_enabled": "VOXERA_PANEL_CSRF_ENABLED",
        "panel_enable_get_mutations": "VOXERA_PANEL_ENABLE_GET_MUTATIONS",
        "queue_lock_stale_s": "VOXERA_QUEUE_LOCK_STALE_S",
        "queue_failed_max_age_s": "VOXERA_QUEUE_FAILED_MAX_AGE_S",
        "queue_failed_max_count": "VOXERA_QUEUE_FAILED_MAX_COUNT",
        "artifacts_retention_days": "VOXERA_ARTIFACTS_RETENTION_DAYS",
        "artifacts_retention_max_count": "VOXERA_ARTIFACTS_RETENTION_MAX_COUNT",
        "queue_prune_max_age_days": "VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS",
        "queue_prune_max_count": "VOXERA_QUEUE_PRUNE_MAX_COUNT",
        "ops_bundle_dir": "VOXERA_OPS_BUNDLE_DIR",
        "dev_mode": "VOXERA_DEV_MODE",
        "notify_enabled": "VOXERA_NOTIFY",
        "vera_web_base_url": "VOXERA_VERA_WEB_BASE_URL",
    }

    resolved: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for field, default_value in defaults.items():
        value = default_value
        source = "default"
        if field in file_values:
            value = file_values[field]
            source = f"file:{path}"
        env_key = env_map[field]
        env_value = (env.get(env_key) or "").strip()
        if env_value != "":
            value = env_value
            source = f"env:{env_key}"
        if field in override_values:
            value = override_values[field]
            source = "override"
        resolved[field] = _coerce(field, value)
        sources[field] = source

    return VoxeraConfig(
        queue_root=resolved["queue_root"],
        panel_host=resolved["panel_host"],
        panel_port=resolved["panel_port"],
        panel_operator_user=resolved["panel_operator_user"],
        panel_operator_password=resolved["panel_operator_password"],
        panel_csrf_enabled=resolved["panel_csrf_enabled"],
        panel_enable_get_mutations=resolved["panel_enable_get_mutations"],
        queue_lock_stale_s=resolved["queue_lock_stale_s"],
        queue_failed_max_age_s=resolved["queue_failed_max_age_s"],
        queue_failed_max_count=resolved["queue_failed_max_count"],
        artifacts_retention_days=resolved["artifacts_retention_days"],
        artifacts_retention_max_count=resolved["artifacts_retention_max_count"],
        queue_prune_max_age_days=resolved["queue_prune_max_age_days"],
        queue_prune_max_count=resolved["queue_prune_max_count"],
        ops_bundle_dir=resolved["ops_bundle_dir"],
        dev_mode=resolved["dev_mode"],
        notify_enabled=resolved["notify_enabled"],
        vera_web_base_url=resolved["vera_web_base_url"],
        config_path=path,
        sources=sources,
    )


def _load_runtime_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    # An empty / whitespace-only file is a common intentional state (e.g. the
    # operator `touch`ed the path, or a previous half-write left nothing
    # behind).  Treat it as empty config rather than crashing setup with a
    # raw JSONDecodeError -- strictly more permissive, never destructive.
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid runtime config JSON at {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid runtime config JSON at {path}: expected top-level object")
    return payload


def update_runtime_config(
    updates: Mapping[str, Any],
    *,
    config_path: Path | None = None,
) -> Path:
    """Merge ``updates`` into the runtime config JSON at ``config_path``.

    Reads the existing JSON (if any), overlays ``updates`` at the top level,
    and atomically writes the result back.  Keys whose value is ``None`` are
    removed from the file so callers can clear a setting explicitly.

    Returns the resolved config path that was written.
    """
    path = resolve_config_path(config_path)
    existing = _load_runtime_config_file(path)
    merged: dict[str, Any] = dict(existing)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _queue_root_default(cwd: Path) -> Path:
    _ = cwd
    return default_queue_root()


def _parse_bool_value(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid value for {key}: expected boolean, got {value!r}")


def _parse_int_value(key: str, value: Any, *, min_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid value for {key}: expected int, got {value!r}") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {parsed}")
    return parsed


def _parse_float_value(key: str, value: Any, *, min_value: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid value for {key}: expected float, got {value!r}") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"Invalid value for {key}: expected >= {min_value}, got {parsed}")
    return parsed


def _coerce(field: str, value: Any) -> Any:
    if field in {"queue_root", "ops_bundle_dir"}:
        if value in (None, ""):
            return None if field == "ops_bundle_dir" else _queue_root_default(Path.cwd())
        return Path(str(value)).expanduser()
    if field in {"panel_host", "panel_operator_user"}:
        text = str(value).strip()
        if not text:
            if field == "panel_operator_user":
                return "admin"
            raise ValueError("Invalid value for panel_host: cannot be empty")
        return text
    if field == "panel_operator_password":
        text = str(value).strip() if value is not None else ""
        return text or None
    if field in {"panel_csrf_enabled", "panel_enable_get_mutations", "dev_mode", "notify_enabled"}:
        return _parse_bool_value(field, value)
    if field == "panel_port":
        return _parse_int_value(field, value, min_value=1)
    if field == "queue_lock_stale_s":
        return _parse_float_value(field, value, min_value=0.0)
    if field == "queue_failed_max_age_s":
        if value in (None, ""):
            return None
        return _parse_float_value(field, value, min_value=0.0)
    if field == "queue_failed_max_count":
        if value in (None, ""):
            return None
        return _parse_int_value(field, value, min_value=1)
    if field == "artifacts_retention_days":
        if value in (None, ""):
            return None
        return _parse_int_value(field, value, min_value=1)
    if field == "artifacts_retention_max_count":
        if value in (None, ""):
            return None
        return _parse_int_value(field, value, min_value=1)
    if field == "queue_prune_max_age_days":
        if value in (None, ""):
            return None
        return _parse_int_value(field, value, min_value=1)
    if field == "queue_prune_max_count":
        if value in (None, ""):
            return None
        return _parse_int_value(field, value, min_value=1)
    if field == "vera_web_base_url":
        text = str(value).strip() if value is not None else ""
        if not text:
            return DEFAULT_VERA_WEB_BASE_URL
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError(
                f"Invalid value for {field}: must start with http:// or https://, got {value!r}"
            )
        return text.rstrip("/")
    return value


def should_load_dotenv(environ: Mapping[str, str] | None = None) -> bool:
    env = environ or os.environ
    raw = str(env.get("VOXERA_LOAD_DOTENV", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


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


def config_fingerprint(settings: VoxeraConfig) -> str:
    serialized = json.dumps(settings.to_safe_dict(), sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


def write_config_fingerprint(
    queue_root: Path,
    settings: VoxeraConfig,
    *,
    filename: str = "_ops/config_snapshot.sha256",
) -> Path:
    queue_root = queue_root.expanduser().resolve()
    out = queue_root / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(config_fingerprint(settings) + "\n", encoding="utf-8")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def write_config_snapshot(
    queue_root: Path,
    settings: VoxeraConfig,
    *,
    filename: str = "config_snapshot.json",
) -> Path:
    queue_root = queue_root.expanduser().resolve()
    out = queue_root / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    generated_at_ms = int(time.time() * 1000)
    payload = {
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "written_at_ms": generated_at_ms,
        "config_path": str(settings.config_path),
        "settings": settings.to_safe_dict(),
        "sources": dict(settings.sources),
    }
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def default_config_path():
    return config_dir() / DEFAULT_CONFIG_NAME


def load_app_config(path=None) -> AppConfig:
    ensure_dirs()
    path = path or default_config_path()
    if not path.exists():
        return AppConfig()
    obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return AppConfig.model_validate(obj)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid app config at {path}: Unknown config key or invalid value. "
            "Check for typos in config.yml fields.\n"
            f"{exc}"
        ) from exc


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
