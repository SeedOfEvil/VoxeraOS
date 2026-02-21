from __future__ import annotations

from platformdirs import user_config_path, user_data_path

APP = "voxera"


def config_dir():
    return user_config_path(APP)


def data_dir():
    return user_data_path(APP)


def ensure_dirs():
    cd = config_dir()
    dd = data_dir()
    cd.mkdir(parents=True, exist_ok=True)
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "audit").mkdir(parents=True, exist_ok=True)
    return cd, dd
