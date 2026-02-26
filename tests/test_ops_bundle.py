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

    out = build_job_bundle(queue_dir, "job-a.json")
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "artifacts/plan.json" in names
        assert len(zf.read("artifacts/stdout.txt")) == 256 * 1024
        assert len(zf.read("artifacts/stderr.txt")) == 256 * 1024
        assert "notes/stdout.txt.truncated.txt" in names
        assert "notes/stderr.txt.truncated.txt" in names


def test_ops_bundle_job_missing_primary_has_note(tmp_path):
    queue_dir = tmp_path / "queue"
    out = build_job_bundle(queue_dir, "job-missing.json")
    with zipfile.ZipFile(out) as zf:
        note = zf.read("notes/job_not_found.txt").decode("utf-8")
        assert "job file not found" in note
