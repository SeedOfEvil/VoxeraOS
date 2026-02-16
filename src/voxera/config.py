from __future__ import annotations

import yaml
from .paths import ensure_dirs, config_dir, data_dir
from .models import AppConfig

DEFAULT_CONFIG_NAME = "config.yml"
DEFAULT_POLICY_NAME = "policy.yml"

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
