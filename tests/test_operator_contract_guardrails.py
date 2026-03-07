from __future__ import annotations

import json
import zipfile

from typer.testing import CliRunner

from voxera import cli
from voxera.config import VoxeraConfig, write_config_snapshot
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.ops_bundle import build_system_bundle


def test_queue_health_json_contract_required_top_level_fields(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(cli.app, ["queue", "health", "--json", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    expected = {
        "health_path",
        "updated_at_ms",
        "daemon_state",
        "consecutive_brain_failures",
        "brain_backoff_wait_s",
        "current_state",
        "recent_history",
        "counters",
        "historical_counters",
        "panel_auth",
        "last_shutdown_outcome",
        "last_shutdown_ts",
        "last_shutdown_reason",
        "last_shutdown_job",
    }
    assert expected.issubset(payload.keys())


def test_assistant_response_artifact_contract_has_required_keys(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)

    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": "advisory answer",
        "provider": "primary",
        "model": "demo-primary",
        "fallback_used": False,
        "fallback_from": None,
        "fallback_reason": None,
        "error_class": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }

    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job = inbox / "job-assistant-contract.json"
    job.write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": "What is the queue state?",
                "thread_id": "thread-contract",
                "advisory": True,
                "read_only": True,
            }
        ),
        encoding="utf-8",
    )

    assert daemon.process_job_file(job) is True

    artifact_path = queue_root / "artifacts" / "job-assistant-contract" / "assistant_response.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    required_keys = {
        "schema_version",
        "kind",
        "thread_id",
        "question",
        "answer",
        "updated_at_ms",
        "answered_at_ms",
        "provider",
        "model",
        "fallback_used",
        "fallback_from",
        "fallback_reason",
        "error_class",
        "advisory_mode",
        "degraded_reason",
        "context",
    }
    assert payload["schema_version"] == 2
    assert payload["kind"] == "assistant_question"
    assert required_keys.issubset(payload.keys())


def test_ops_bundle_system_manifest_contract_fields(tmp_path, monkeypatch):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "health.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    monkeypatch.setattr(
        "voxera.ops_bundle.subprocess.check_output",
        lambda *args, **kwargs: "journal lines\n",
    )

    out = build_system_bundle(queue_dir)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert set(manifest.keys()) == {"timestamp_ms", "voxera_version", "git_sha", "queue_root"}
        assert isinstance(manifest["timestamp_ms"], int)
        assert isinstance(manifest["voxera_version"], str)
        assert manifest["queue_root"] == str(queue_dir.resolve())


def test_config_snapshot_contract_shape_required_fields(tmp_path):
    cfg = VoxeraConfig(
        queue_root=tmp_path,
        panel_host="127.0.0.1",
        panel_port=8844,
        panel_operator_user="admin",
        panel_operator_password="secret",
        panel_csrf_enabled=True,
        panel_enable_get_mutations=False,
        queue_lock_stale_s=3600.0,
        queue_failed_max_age_s=None,
        queue_failed_max_count=None,
        artifacts_retention_days=None,
        artifacts_retention_max_count=None,
        queue_prune_max_age_days=None,
        queue_prune_max_count=None,
        ops_bundle_dir=None,
        dev_mode=False,
        notify_enabled=False,
        config_path=tmp_path / "runtime.json",
        sources={},
    )

    out = write_config_snapshot(tmp_path, cfg)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert set(payload.keys()) == {
        "schema_version",
        "generated_at_ms",
        "written_at_ms",
        "config_path",
        "settings",
        "sources",
    }
    assert payload["schema_version"] == 1
    assert isinstance(payload["settings"], dict)
    assert isinstance(payload["sources"], dict)
