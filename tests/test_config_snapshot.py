from __future__ import annotations

import json
from pathlib import Path

from voxera.config import VoxeraConfig, write_config_snapshot


def test_write_config_snapshot_creates_redacted_payload(tmp_path: Path) -> None:
    cfg = VoxeraConfig(
        queue_root=tmp_path,
        panel_host="127.0.0.1",
        panel_port=8844,
        panel_operator_user="admin",
        panel_operator_password="super-secret",
        panel_csrf_enabled=True,
        panel_enable_get_mutations=False,
        queue_lock_stale_s=3600.0,
        queue_failed_max_age_s=None,
        queue_failed_max_count=None,
        artifacts_retention_days=None,
        artifacts_retention_max_count=None,
        ops_bundle_dir=None,
        dev_mode=False,
        notify_enabled=False,
        config_path=Path("/tmp/runtime.json"),
        sources={"panel_operator_password": "env:VOXERA_PANEL_OPERATOR_PASSWORD"},
    )

    out = write_config_snapshot(tmp_path, cfg)

    assert out == tmp_path / "config_snapshot.json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["settings"]["panel_operator_password"] == "***"
    assert payload["sources"]["panel_operator_password"] == "env:VOXERA_PANEL_OPERATOR_PASSWORD"
    assert isinstance(payload["generated_at_ms"], int)
    assert isinstance(payload["written_at_ms"], int)
    assert payload["config_path"] == "/tmp/runtime.json"
