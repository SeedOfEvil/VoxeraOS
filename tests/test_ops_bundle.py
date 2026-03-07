import json
import zipfile

from voxera.ops_bundle import build_job_bundle, build_system_bundle


def test_ops_bundle_system_contains_manifest_and_health(tmp_path, monkeypatch):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "health.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    monkeypatch.setattr(
        "voxera.ops_bundle.subprocess.check_output",
        lambda *args, **kwargs: "journal lines\n",
    )

    out = build_system_bundle(queue_dir)
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "snapshots/queue_health.json" in names
        payload = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert payload["queue_root"] == str(queue_dir.resolve())


def test_ops_bundle_job_includes_artifacts_and_truncates_large_streams(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-a"
    art.mkdir(parents=True)
    (art / "plan.json").write_text("{}", encoding="utf-8")
    (art / "actions.jsonl").write_text("{}\n", encoding="utf-8")
    (art / "stdout.txt").write_text("x" * (300 * 1024), encoding="utf-8")
    (art / "stderr.txt").write_text("y" * (300 * 1024), encoding="utf-8")
    (art / "execution_result.json").write_text("{}", encoding="utf-8")

    out = build_job_bundle(queue_dir, "job-a.json")
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "artifacts/plan.json" in names
        assert len(zf.read("artifacts/stdout.txt")) == 256 * 1024
        assert len(zf.read("artifacts/stderr.txt")) == 256 * 1024
        assert "artifacts/execution_result.json" in names
        assert "notes/stdout.txt.truncated.txt" in names
        assert "notes/stderr.txt.truncated.txt" in names


def test_ops_bundle_job_writes_structured_execution_summary_note(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-a"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "succeeded",
                "lifecycle_state": "done",
                "last_attempted_step": 1,
                "last_completed_step": 1,
                "approval_status": "approved",
                "step_results": [
                    {
                        "step_index": 1,
                        "status": "succeeded",
                        "summary": "done summary",
                        "operator_note": "note",
                        "next_action_hint": "none",
                        "output_artifacts": ["outputs/a.txt"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    out = build_job_bundle(queue_dir, "job-a.json")

    with zipfile.ZipFile(out) as zf:
        payload = json.loads(zf.read("notes/structured_execution_summary.json").decode("utf-8"))
        assert payload["terminal_outcome"] == "succeeded"
        assert payload["lifecycle_state"] == "done"
        assert payload["latest_summary"] == "done summary"
        assert payload["operator_note"] == "note"
        assert payload["output_artifacts"] == ["outputs/a.txt"]


def test_ops_bundle_job_missing_primary_has_note(tmp_path):
    queue_dir = tmp_path / "queue"
    out = build_job_bundle(queue_dir, "job-missing.json")
    with zipfile.ZipFile(out) as zf:
        note = zf.read("notes/job_not_found.txt").decode("utf-8")
        assert "job file not found" in note


def test_ops_bundle_respects_explicit_archive_dir(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")
    archive_dir = tmp_path / "incident-123"

    system_out = build_system_bundle(queue_dir, archive_dir=archive_dir)
    job_out = build_job_bundle(queue_dir, "job-a.json", archive_dir=archive_dir)

    assert system_out == archive_dir.resolve() / "bundle-system.zip"
    assert job_out == archive_dir.resolve() / "bundle-job-job-a.zip"
    assert system_out.exists()
    assert job_out.exists()


def test_ops_bundle_uses_env_archive_dir_when_dir_not_passed(tmp_path, monkeypatch):
    queue_dir = tmp_path / "queue"
    env_archive = tmp_path / "env-archive"
    monkeypatch.setenv("VOXERA_OPS_BUNDLE_DIR", str(env_archive))

    out = build_system_bundle(queue_dir)

    assert out.parent == env_archive.resolve()
    assert out.exists()


def test_ops_bundle_default_archive_under_queue_archive(tmp_path, monkeypatch):
    queue_dir = tmp_path / "queue"
    monkeypatch.delenv("VOXERA_OPS_BUNDLE_DIR", raising=False)

    out = build_system_bundle(queue_dir)

    assert out.exists()
    assert out.name == "bundle-system.zip"
    assert out.parent.parent == (queue_dir.resolve() / "_archive")


def test_ops_bundle_job_uses_single_optional_note_for_absent_context_artifacts(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")

    out = build_job_bundle(queue_dir, "job-a.json")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "notes/optional_context_artifacts_absent.txt" in names
        assert "notes/approval_not_found.txt" not in names
        assert "notes/failed_sidecar_not_found.txt" not in names


def test_ops_bundle_job_emits_anomaly_note_when_failed_sidecar_expected_but_missing(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "failed" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")

    out = build_job_bundle(queue_dir, "job-a.json")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "notes/failed_sidecar_missing_unexpected.txt" in names
