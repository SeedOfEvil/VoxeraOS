import json

from typer.testing import CliRunner

from voxera import cli


def test_demo_creates_jobs_and_checklist(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"

    result = runner.invoke(cli.app, ["demo", "--queue-dir", str(queue_dir), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert len(payload["created_jobs"]) == 2
    assert any(name.startswith("demo-basic-") for name in payload["created_jobs"])
    assert any(name.startswith("demo-approval-") for name in payload["created_jobs"])

    approval_job = (
        queue_dir
        / "inbox"
        / next(n for n in payload["created_jobs"] if n.startswith("demo-approval-"))
    )
    approval_payload = json.loads(approval_job.read_text(encoding="utf-8"))
    assert approval_payload["approval_required"] is True

    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["queue directories"] == "PASS"
    assert statuses["demo jobs"] == "PASS"
    assert statuses["provider readiness (online)"] == "SKIPPED"


def test_demo_online_missing_keys_skips_and_exits_zero(tmp_path, monkeypatch):
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    runner = CliRunner()
    queue_dir = tmp_path / "queue"

    result = runner.invoke(
        cli.app,
        ["demo", "--queue-dir", str(queue_dir), "--online", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    provider = next(
        item for item in payload["checks"] if item["name"] == "provider readiness (online)"
    )
    assert provider["status"] == "SKIPPED"
