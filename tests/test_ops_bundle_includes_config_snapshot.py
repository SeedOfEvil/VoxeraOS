from __future__ import annotations

import json
import zipfile
from pathlib import Path

from voxera.config import load_config
from voxera.ops_bundle import build_job_bundle, build_system_bundle


def _write_runtime_config(path: Path, queue_root: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "queue_root": str(queue_root),
                "panel_operator_password": "top-secret",
                "panel_operator_user": "ops",
            }
        ),
        encoding="utf-8",
    )


def test_system_and_job_bundle_include_config_snapshot(tmp_path: Path, monkeypatch) -> None:
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")
    cfg_path = tmp_path / "runtime.json"
    _write_runtime_config(cfg_path, queue_dir)

    monkeypatch.setattr("voxera.ops_bundle.subprocess.check_output", lambda *a, **k: "journal\n")
    monkeypatch.setattr(
        "voxera.ops_bundle.load_runtime_config",
        lambda *args, **kwargs: load_config(config_path=cfg_path),
    )

    system_out = build_system_bundle(queue_dir)
    job_out = build_job_bundle(queue_dir, "job-a.json")

    with zipfile.ZipFile(system_out) as zf:
        assert "snapshots/config_snapshot.json" in zf.namelist()
        assert "snapshots/config_snapshot.sha256" in zf.namelist()
        snapshot = json.loads(zf.read("snapshots/config_snapshot.json").decode("utf-8"))
        assert snapshot["schema_version"] == 1
        assert snapshot["settings"]["panel_operator_password"] == "***"

    with zipfile.ZipFile(job_out) as zf:
        assert "snapshots/config_snapshot.json" in zf.namelist()
        assert "snapshots/config_snapshot.sha256" in zf.namelist()
        snapshot = json.loads(zf.read("snapshots/config_snapshot.json").decode("utf-8"))
        assert snapshot["schema_version"] == 1
        assert snapshot["settings"]["panel_operator_password"] == "***"
