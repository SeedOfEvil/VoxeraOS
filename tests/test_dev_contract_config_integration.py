from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.ops_bundle import _resolve_archive_dir
from voxera.panel import app as panel_app


def test_makefile_targets_require_dev_marker() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    for target in ["fmt-check", "lint", "type", "test", "test-failed-sidecar", "release-check"]:
        assert f"{target}: $(DEV_MARKER)" in makefile


def test_queue_lock_stale_seconds_read_from_runtime_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOXERA_QUEUE_LOCK_STALE_S", "777")
    daemon = MissionQueueDaemon(queue_root=tmp_path / "queue")
    assert daemon.lock_stale_after_s == 777.0


def test_ops_bundle_archive_dir_env_and_explicit_override(tmp_path, monkeypatch) -> None:
    queue_root = tmp_path / "queue"
    queue_root.mkdir(parents=True)
    env_archive = tmp_path / "env_archive"
    explicit_archive = tmp_path / "explicit_archive"

    monkeypatch.setenv("VOXERA_OPS_BUNDLE_DIR", str(env_archive))
    resolved_env = _resolve_archive_dir(queue_root, None)
    assert resolved_env == env_archive.resolve()

    resolved_explicit = _resolve_archive_dir(queue_root, explicit_archive)
    assert resolved_explicit == explicit_archive.resolve()


def test_panel_operator_defaults_to_admin_and_missing_password_raises(
    tmp_path, monkeypatch
) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_app.Path, "home", lambda: fake_home)
    monkeypatch.delenv("VOXERA_PANEL_OPERATOR_USER", raising=False)
    monkeypatch.delenv("VOXERA_PANEL_OPERATOR_PASSWORD", raising=False)

    class _Req:
        url = type("u", (), {"path": "/"})
        method = "GET"
        client = type("c", (), {"host": "127.0.0.1"})

    with pytest.raises(HTTPException) as exc:
        panel_app._operator_credentials(_Req())
    assert exc.value.status_code == 503
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    user, _password = panel_app._operator_credentials(_Req())
    assert user == "admin"
