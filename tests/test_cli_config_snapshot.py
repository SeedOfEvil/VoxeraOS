from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from voxera import cli
from voxera.config import load_config


def _write_runtime_config(path: Path, queue_root: Path, password: str = "secret") -> None:
    path.write_text(
        json.dumps(
            {
                "queue_root": str(queue_root),
                "panel_operator_password": password,
                "panel_operator_user": "admin",
            }
        ),
        encoding="utf-8",
    )


def test_config_snapshot_default_writes_to_queue_root_without_queue_jobs(
    tmp_path: Path, monkeypatch
) -> None:
    queue_root = tmp_path / "queue"
    cfg_path = tmp_path / "runtime.json"
    _write_runtime_config(cfg_path, queue_root, password="very-secret")
    monkeypatch.setattr("voxera.cli.load_runtime_config", lambda: load_config(config_path=cfg_path))

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "snapshot"])

    assert result.exit_code == 0
    line = result.stdout.strip().splitlines()
    assert len(line) == 1
    out_path = Path(line[0])
    assert out_path.is_absolute()
    assert out_path == (queue_root / "config_snapshot.json").resolve()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["settings"]["panel_operator_password"] == "***"
    assert "very-secret" not in out_path.read_text(encoding="utf-8")

    for sub in ("inbox", "pending", "done", "failed"):
        assert not (queue_root / sub).exists()


def test_config_snapshot_respects_out_and_dir(tmp_path: Path, monkeypatch) -> None:
    queue_root = tmp_path / "queue"
    cfg_path = tmp_path / "runtime.json"
    _write_runtime_config(cfg_path, queue_root)
    monkeypatch.setattr("voxera.cli.load_runtime_config", lambda: load_config(config_path=cfg_path))
    runner = CliRunner()

    out_file = tmp_path / "custom" / "snap.json"
    out_res = runner.invoke(cli.app, ["config", "snapshot", "--out", str(out_file)])
    assert out_res.exit_code == 0
    assert Path(out_res.stdout.strip()) == out_file.resolve()
    assert out_file.exists()

    out_dir = tmp_path / "snapshot-dir"
    dir_res = runner.invoke(cli.app, ["config", "snapshot", "--dir", str(out_dir)])
    assert dir_res.exit_code == 0
    expected = out_dir.resolve() / "config_snapshot.json"
    assert Path(dir_res.stdout.strip()) == expected
    assert expected.exists()
