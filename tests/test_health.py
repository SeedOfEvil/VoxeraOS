from voxera.health import _normalize_health_snapshot, read_health_snapshot, record_last_shutdown


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

    data = read_health_snapshot(queue_root)
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


def test_health_normalization_deterministic_defaults_for_observability_fields():
    payload = _normalize_health_snapshot({"counters": "bad", "panel_auth": []})
    assert payload["daemon_state"] == "healthy"
    assert payload["consecutive_brain_failures"] == 0
    assert payload["brain_backoff_wait_s"] == 0
    assert payload["brain_backoff_active"] is False
    assert payload["daemon_started_at_ms"] is None
    assert payload["daemon_pid"] is None
    assert payload["updated_at_ms"] is None
    assert payload["last_ok_event"] is None
    assert payload["last_ok_ts_ms"] is None
    assert payload["last_error"] is None
    assert payload["last_error_ts_ms"] is None
    assert payload["last_fallback_reason"] is None
    assert payload["last_fallback_from"] is None
    assert payload["last_fallback_to"] is None
    assert payload["last_fallback_ts_ms"] is None
    assert payload["counters"] == {}
    assert payload["panel_auth"] == {}


def test_reset_health_snapshot_scopes_preserve_counters(tmp_path):
    from voxera.health import write_health_snapshot
    from voxera.health_reset import reset_health_snapshot

    queue_root = tmp_path / "queue"
    write_health_snapshot(
        queue_root,
        {
            "daemon_state": "degraded",
            "consecutive_brain_failures": 4,
            "degraded_reason": "brain_fallbacks",
            "last_error": "boom",
            "last_error_ts_ms": 12,
            "last_fallback_reason": "timeout",
            "last_shutdown_outcome": "failed_shutdown",
            "counters": {"panel_401_count": 9, "brain_fallback_count": 7},
        },
    )

    reset_health_snapshot(queue_root, scope="current_state", actor_surface="test")
    current_only = read_health_snapshot(queue_root)
    assert current_only["daemon_state"] == "healthy"
    assert current_only["consecutive_brain_failures"] == 0
    assert current_only["last_error"] == "boom"
    assert current_only["counters"]["panel_401_count"] == 9

    reset_health_snapshot(queue_root, scope="recent_history", actor_surface="test")
    recent_only = read_health_snapshot(queue_root)
    assert recent_only["last_error"] is None
    assert recent_only["last_shutdown_outcome"] is None
    assert recent_only["counters"]["brain_fallback_count"] == 7


def test_reset_health_snapshot_counter_group_is_selective(tmp_path):
    from voxera.health import write_health_snapshot
    from voxera.health_reset import reset_health_snapshot

    queue_root = tmp_path / "queue"
    write_health_snapshot(
        queue_root,
        {
            "counters": {
                "panel_401_count": 2,
                "panel_403_count": 1,
                "brain_fallback_count": 5,
                "other_counter": 3,
            }
        },
    )

    reset_health_snapshot(
        queue_root,
        scope="current_and_recent",
        counter_group="panel_auth_counters",
        actor_surface="test",
    )
    payload = read_health_snapshot(queue_root)
    assert payload["counters"]["panel_401_count"] == 0
    assert payload["counters"]["panel_403_count"] == 0
    assert payload["counters"]["brain_fallback_count"] == 5
    assert payload["counters"]["other_counter"] == 3
