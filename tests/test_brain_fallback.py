"""Deterministic tests for brain fallback reason classification, health counters, and doctor output."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from voxera.brain.fallback import (
    ALL_REASONS,
    AUTH,
    MALFORMED,
    NETWORK,
    RATE_LIMIT,
    TIMEOUT,
    UNKNOWN,
    classify_fallback_reason,
)
from voxera.health import read_health_snapshot, record_fallback_transition

# ---------------------------------------------------------------------------
# 1. Classifier correctness for each enum category
# ---------------------------------------------------------------------------


class TestClassifyFallbackReason:
    """Each reason category maps from representative exceptions."""

    def test_asyncio_timeout_error(self):
        assert classify_fallback_reason(asyncio.TimeoutError()) == TIMEOUT

    def test_builtin_timeout_error(self):
        assert classify_fallback_reason(TimeoutError("connection timed out")) == TIMEOUT

    def test_httpx_timeout_exception(self):
        assert classify_fallback_reason(httpx.TimeoutException("timed out")) == TIMEOUT

    def test_message_contains_timed_out(self):
        assert (
            classify_fallback_reason(RuntimeError("Planner timed out contacting Gemini")) == TIMEOUT
        )

    def test_429_rate_limit(self):
        req = httpx.Request("POST", "https://api.example.com/v1/chat")
        resp = httpx.Response(status_code=429, request=req, text="rate limited")
        exc = httpx.HTTPStatusError("429", request=req, response=resp)
        assert classify_fallback_reason(exc) == RATE_LIMIT

    def test_rate_limit_message(self):
        assert classify_fallback_reason(RuntimeError("Gemini rate limit (429)")) == RATE_LIMIT

    def test_401_auth(self):
        req = httpx.Request("POST", "https://api.example.com/v1/chat")
        resp = httpx.Response(status_code=401, request=req, text="unauthorized")
        exc = httpx.HTTPStatusError("401", request=req, response=resp)
        assert classify_fallback_reason(exc) == AUTH

    def test_403_auth(self):
        req = httpx.Request("POST", "https://api.example.com/v1/chat")
        resp = httpx.Response(status_code=403, request=req, text="forbidden")
        exc = httpx.HTTPStatusError("403", request=req, response=resp)
        assert classify_fallback_reason(exc) == AUTH

    def test_unauthorized_message(self):
        assert (
            classify_fallback_reason(RuntimeError("Gemini provider error HTTP 401: unauthorized"))
            == AUTH
        )

    def test_json_decode_malformed(self):
        assert classify_fallback_reason(json.JSONDecodeError("bad", "", 0)) == MALFORMED

    def test_malformed_provider_output(self):
        assert (
            classify_fallback_reason(RuntimeError("Planner returned malformed provider output"))
            == MALFORMED
        )

    def test_non_json_output(self):
        assert (
            classify_fallback_reason(RuntimeError("Planner returned non-JSON output: xyz"))
            == MALFORMED
        )

    def test_connection_error_network(self):
        assert classify_fallback_reason(ConnectionError("Connection refused")) == NETWORK

    def test_httpx_connect_error(self):
        assert classify_fallback_reason(httpx.ConnectError("DNS resolution failed")) == NETWORK

    def test_dns_resolution_message(self):
        assert classify_fallback_reason(RuntimeError("name resolution failed")) == NETWORK

    def test_connection_reset_message(self):
        assert classify_fallback_reason(RuntimeError("connection reset by peer")) == NETWORK

    def test_unknown_generic_error(self):
        assert classify_fallback_reason(RuntimeError("something unexpected")) == UNKNOWN

    def test_unknown_value_error(self):
        assert classify_fallback_reason(ValueError("bad value")) == UNKNOWN

    def test_all_reasons_is_complete(self):
        """ALL_REASONS contains exactly the six documented categories."""
        assert {TIMEOUT, AUTH, RATE_LIMIT, MALFORMED, NETWORK, UNKNOWN} == ALL_REASONS


# ---------------------------------------------------------------------------
# 2. Fallback transition increments counters + updates last_fallback_* fields
# ---------------------------------------------------------------------------


class TestRecordFallbackTransition:
    def test_increments_counters(self, tmp_path: Path):
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        record_fallback_transition(queue_root, from_tier="primary", to_tier="fast", reason=TIMEOUT)

        snap = read_health_snapshot(queue_root)
        counters = snap.get("counters", {})
        assert counters["brain_fallback_count"] == 1
        assert counters["brain_fallback_reason_timeout"] == 1

    def test_multiple_increments(self, tmp_path: Path):
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        record_fallback_transition(
            queue_root, from_tier="primary", to_tier="fast", reason=RATE_LIMIT
        )
        record_fallback_transition(queue_root, from_tier="fast", to_tier="fallback", reason=TIMEOUT)
        record_fallback_transition(
            queue_root, from_tier="primary", to_tier="fast", reason=RATE_LIMIT
        )

        snap = read_health_snapshot(queue_root)
        counters = snap.get("counters", {})
        assert counters["brain_fallback_count"] == 3
        assert counters["brain_fallback_reason_rate_limit"] == 2
        assert counters["brain_fallback_reason_timeout"] == 1

    def test_updates_last_fallback_fields(self, tmp_path: Path):
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        record_fallback_transition(queue_root, from_tier="primary", to_tier="fast", reason=AUTH)

        snap = read_health_snapshot(queue_root)
        assert snap["last_fallback_reason"] == AUTH
        assert snap["last_fallback_from"] == "primary"
        assert snap["last_fallback_to"] == "fast"
        assert isinstance(snap["last_fallback_ts_ms"], int)

    def test_last_fallback_reflects_most_recent(self, tmp_path: Path):
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        record_fallback_transition(queue_root, from_tier="primary", to_tier="fast", reason=TIMEOUT)
        record_fallback_transition(queue_root, from_tier="fast", to_tier="fallback", reason=NETWORK)

        snap = read_health_snapshot(queue_root)
        assert snap["last_fallback_reason"] == NETWORK
        assert snap["last_fallback_from"] == "fast"
        assert snap["last_fallback_to"] == "fallback"

    def test_preserves_existing_health_fields(self, tmp_path: Path):
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        # Pre-populate with existing health data.
        health_path = queue_root / "health.json"
        health_path.write_text(
            json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 12345}),
            encoding="utf-8",
        )

        record_fallback_transition(
            queue_root, from_tier="primary", to_tier="fast", reason=MALFORMED
        )

        snap = read_health_snapshot(queue_root)
        assert snap["last_ok_event"] == "daemon_tick"
        assert snap["last_ok_ts_ms"] == 12345
        assert snap["last_fallback_reason"] == MALFORMED


# ---------------------------------------------------------------------------
# 3. voxera doctor --quick includes the fallback line
# ---------------------------------------------------------------------------


class TestDoctorQuickFallback:
    def _make_queue(self, tmp_path: Path) -> Path:
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        return queue_root

    def test_doctor_quick_shows_fallback_when_present(self, tmp_path: Path):
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        (queue_root / "health.json").write_text(
            json.dumps(
                {
                    "last_ok_event": "daemon_tick",
                    "last_ok_ts_ms": 100000,
                    "last_fallback_reason": "RATE_LIMIT",
                    "last_fallback_from": "primary",
                    "last_fallback_to": "fast",
                    "last_fallback_ts_ms": 99000,
                }
            ),
            encoding="utf-8",
        )

        checks = run_quick_doctor(queue_root=queue_root)

        fallback_check = next(item for item in checks if item["check"] == "last fallback")
        assert fallback_check["status"] == "warn"
        assert "primary -> fast" in fallback_check["detail"]
        assert "reason=RATE_LIMIT" in fallback_check["detail"]
        assert "ts=99000" in fallback_check["detail"]

    def test_doctor_quick_shows_none_when_no_fallback(self, tmp_path: Path):
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        (queue_root / "health.json").write_text(
            json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 100000}),
            encoding="utf-8",
        )

        checks = run_quick_doctor(queue_root=queue_root)

        fallback_check = next(item for item in checks if item["check"] == "last fallback")
        assert fallback_check["status"] == "ok"
        assert fallback_check["detail"] == "none"

    def test_doctor_quick_shows_hint_for_known_reasons(self, tmp_path: Path):
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        (queue_root / "health.json").write_text(
            json.dumps(
                {
                    "last_ok_event": "daemon_tick",
                    "last_ok_ts_ms": 100000,
                    "last_fallback_reason": "AUTH",
                    "last_fallback_from": "primary",
                    "last_fallback_to": "fallback",
                    "last_fallback_ts_ms": 98000,
                }
            ),
            encoding="utf-8",
        )

        checks = run_quick_doctor(queue_root=queue_root)

        fallback_check = next(item for item in checks if item["check"] == "last fallback")
        assert "AUTH implies bad key/config" in fallback_check["hint"]
