import json

from voxera.health import _normalize_health_snapshot, record_last_shutdown


def test_health_normalization_adds_last_shutdown_defaults():
    payload = _normalize_health_snapshot({})
    assert payload["last_shutdown_outcome"] is None
    assert payload["last_shutdown_ts"] is None
    assert payload["last_shutdown_reason"] is None
    assert payload["last_shutdown_job"] is None


def test_health_normalization_preserves_valid_last_shutdown_fields():
    payload = _normalize_health_snapshot(
        {
            "last_shutdown_outcome": "clean",
            "last_shutdown_ts": 1712345678.5,
            "last_shutdown_reason": "SIGTERM",
            "last_shutdown_job": "job-1.json",
        }
    )
    assert payload["last_shutdown_outcome"] == "clean"
    assert payload["last_shutdown_ts"] == 1712345678.5
    assert payload["last_shutdown_reason"] == "SIGTERM"
    assert payload["last_shutdown_job"] == "job-1.json"


def test_record_last_shutdown_clean_and_failed(tmp_path):
    queue_root = tmp_path / "queue"
    clean = record_last_shutdown(
        queue_root,
        outcome="clean",
        reason="SIGTERM",
        job=None,
        now_fn=lambda: 100.25,
    )
    assert clean["last_shutdown_outcome"] == "clean"
    assert clean["last_shutdown_reason"] == "SIGTERM"
    assert clean["last_shutdown_ts"] == 100.25
    assert clean["last_shutdown_job"] is None

    failed = record_last_shutdown(
        queue_root,
        outcome="failed_shutdown",
        reason="RuntimeError: boom",
        job="job-9.json",
        now_fn=lambda: 200.5,
    )
    assert failed["last_shutdown_outcome"] == "failed_shutdown"
    assert failed["last_shutdown_reason"] == "RuntimeError: boom"
    assert failed["last_shutdown_ts"] == 200.5
    assert failed["last_shutdown_job"] == "job-9.json"

    data = json.loads((queue_root / "health.json").read_text(encoding="utf-8"))
    assert data["last_shutdown_outcome"] == "failed_shutdown"


def test_record_last_shutdown_reason_is_bounded(tmp_path):
    queue_root = tmp_path / "queue"
    reason = "x" * 600
    payload = record_last_shutdown(
        queue_root,
        outcome="failed_shutdown",
        reason=reason,
        job="job-1.json",
        now_fn=lambda: 3.0,
    )
    assert len(payload["last_shutdown_reason"]) <= 240
    assert payload["last_shutdown_reason"].endswith("…")


def test_record_last_shutdown_invalid_outcome_falls_back_to_failed(tmp_path):
    queue_root = tmp_path / "queue"
    payload = record_last_shutdown(
        queue_root,
        outcome="unknown",
        reason="bad",
        job=None,
        now_fn=lambda: 9.0,
    )
    assert payload["last_shutdown_outcome"] == "failed_shutdown"
