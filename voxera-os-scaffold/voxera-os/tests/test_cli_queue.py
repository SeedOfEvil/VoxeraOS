from typer.testing import CliRunner

from voxera import cli


def test_queue_init_creates_expected_directories(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"

    result = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert queue_dir.exists()
    assert (queue_dir / "pending").exists()
    assert (queue_dir / "pending" / "approvals").exists()
    assert (queue_dir / "done").exists()
    assert (queue_dir / "failed").exists()


def test_queue_init_is_idempotent(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)

    first = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])
    second = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (queue_dir / "pending" / "approvals").exists()
    assert (queue_dir / "done").exists()
    assert (queue_dir / "failed").exists()
