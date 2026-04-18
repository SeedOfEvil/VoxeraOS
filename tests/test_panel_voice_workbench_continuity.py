"""Tests for the Voice Workbench session-continuity polish pass.

Pins the operator-facing continuity framing that was added to make it
obvious which canonical Vera session the workbench is writing into and
to surface a clean "continue in Vera" affordance.

Covers:

1. A session-continuity banner renders on the initial voice status
   page (before any workbench run) and in the workbench result block.
2. The banner contains the session id and a ``Continue in Vera`` link
   that carries ``?session_id=<id>`` so the canonical Vera surface can
   pick up the same session.
3. ``/voice/status`` adopts the operator's existing
   ``vera_session_id`` cookie instead of minting a fresh session each
   page load, so the workbench and canonical Vera chat share a
   session out of the box.
4. The workbench result renders a "Current run" framing with a run
   timestamp so old and new state cannot visually blur.
5. The framing distinguishes "new session" (no prior turns) from
   "continuing session" (N prior turns already recorded).
6. The path-traversal clamp applied to the session id flows into the
   ``Continue in Vera`` link so it can only ever resolve to a session
   under the canonical sessions directory.
7. The canonical ``vera_web`` ``GET /`` route accepts
   ``?session_id=<id>`` as a query-parameter override and sets the
   ``vera_session_id`` cookie so a link from the workbench lands the
   operator in the intended session.
8. The truth-model wording around the governed handoff lane is
   preserved (``not attempted`` + no queue/preview success claims).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.vera import session_store
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(client: TestClient, method: str, url: str, *, data: dict[str, str]):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data)
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


@pytest.fixture()
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
    monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
    monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "piper_local")
    return queue_dir


def _make_stt_response(
    *,
    transcript: str | None = "please check system health",
    language: str | None = "en",
) -> STTResponse:
    return STTResponse(
        request_id="test-stt-cty",
        status=STT_STATUS_SUCCEEDED,
        transcript=transcript,
        language=language,
        audio_duration_ms=2000,
        error=None,
        error_class=None,
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1100,
        schema_version=1,
        inference_ms=100,
    )


async def _fake_vera_reply(**kwargs: Any) -> dict[str, Any]:
    return {"answer": f"Ack: {kwargs['user_message']}", "status": "ok:test"}


class TestInitialPageContinuityBanner:
    """The continuity banner renders on first page load, before any run."""

    def test_banner_renders_on_initial_voice_status_page(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'data-testid="voice-workbench-continuity"' in res.text
        assert "Vera session" in res.text
        assert 'data-testid="voice-workbench-continue-in-vera"' in res.text
        assert "Continue in Vera" in res.text

    def test_banner_shows_new_session_label_when_no_prior_turns(self, _panel_env: Path) -> None:
        """A freshly-minted session has zero prior turns — render as 'new session'."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "new session" in res.text

    def test_banner_surfaces_prior_turn_count_for_existing_session(self, _panel_env: Path) -> None:
        """When an existing Vera session is active on the cookie, the banner
        must show its real turn count instead of 'new session'."""
        queue_dir = _panel_env
        session_id = "vera-wb-cty-existing"
        session_store.append_session_turn(queue_dir, session_id, role="user", text="first")
        session_store.append_session_turn(queue_dir, session_id, role="assistant", text="reply")

        client = TestClient(panel_module.app)
        client.cookies.set("vera_session_id", session_id)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert session_id in res.text
        assert "2 turns recorded" in res.text
        # Banner's continue-in-Vera link must carry the session id so
        # the canonical Vera surface picks up the same session.
        assert f"/vera?session_id={session_id}" in res.text


class TestContinueInVeraLinkOnResult:
    """After a workbench run, the banner and handoff block link to Vera."""

    def test_continue_in_vera_link_is_rendered_with_session_id(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="ping")
        session_id = "vera-wb-cty-link"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        # Both the banner and the governed-handoff block should link to
        # the same canonical session via the ``?session_id=`` query.
        assert f"/vera?session_id={session_id}" in res.text
        # Text-level affordance must be obvious to the operator.
        assert "Continue in Vera" in res.text

    def test_path_traversal_session_id_is_clamped_in_continue_link(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malicious session ids must be clamped before they reach the
        ``Continue in Vera`` href so the link can only point into the
        canonical sessions directory."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="clamp")
        malicious = "../../etc/evil"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": malicious,
                },
            )
        assert res.status_code == 200
        # The clamped component is "evil" — never the original traversal.
        assert "/vera?session_id=evil" in res.text
        assert "/vera?session_id=../../etc/evil" not in res.text


class TestCurrentRunFraming:
    """Pin stale-result protection: the result block is clearly 'this run'."""

    def test_current_run_label_is_rendered_after_a_run(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert 'data-testid="voice-workbench-run-meta"' in res.text
        assert "Current run" in res.text
        # A real monotonic started_at_ms is rendered so operators can
        # tell which run they're looking at.
        assert "started_at_ms=" in res.text

    def test_new_session_framing_when_no_prior_turns(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="fresh")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    # No prior session id → the route mints a fresh one.
                },
            )
        assert res.status_code == 200
        # In the "Current run" line, this is a brand-new session, so
        # the framing must say "new session" and NOT "continuing session".
        run_meta_start = res.text.index('data-testid="voice-workbench-run-meta"')
        run_meta_end = res.text.index("</div>", run_meta_start)
        run_meta_block = res.text[run_meta_start:run_meta_end]
        assert "new session" in run_meta_block
        assert "continuing session" not in run_meta_block

    def test_continuing_session_framing_when_prior_turns_exist(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the operator supplies a session id that already has turns,
        the framing must say 'continuing session' with the prior turn
        count rather than 'new session'."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-cty-prior"
        session_store.append_session_turn(queue_dir, session_id, role="user", text="earlier")
        session_store.append_session_turn(queue_dir, session_id, role="assistant", text="ok")

        stt = _make_stt_response(transcript="next")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        run_meta_start = res.text.index('data-testid="voice-workbench-run-meta"')
        run_meta_end = res.text.index("</div>", run_meta_start)
        run_meta_block = res.text[run_meta_start:run_meta_end]
        assert "continuing session" in run_meta_block
        assert "2 prior turns" in run_meta_block


class TestSessionCookieAdoption:
    """Workbench page adopts the operator's existing Vera session cookie."""

    def test_voice_status_page_adopts_existing_vera_session_cookie(self, _panel_env: Path) -> None:
        session_id = "vera-wb-cty-cookie"
        client = TestClient(panel_module.app)
        client.cookies.set("vera_session_id", session_id)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # The hidden form input + banner both use the cookie session id
        # rather than minting a fresh one.
        assert f'value="{session_id}"' in res.text
        assert session_id in res.text


class TestTruthModelPreservation:
    """The continuity polish must not weaken truth-model wording."""

    def test_governed_handoff_still_reads_not_attempted(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="do the thing")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert "Governed Handoff" in res.text
        assert "not attempted" in res.text
        assert "No queue preview was drafted" in res.text
        assert "no queue job was submitted" in res.text.lower()
        lowered = res.text.lower()
        assert "job submitted" not in lowered
        assert "has been submitted" not in lowered
        assert "executed successfully" not in lowered

    def test_continue_in_vera_link_on_handoff_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The handoff block must carry the concrete continue-in-Vera link
        that ties back to the exact session the operator just used."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        session_id = "vera-wb-cty-handoff"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        # At least one anchor to /vera?session_id=<id> must appear on the
        # page (banner). The handoff block links to the same target.
        assert f'href="/vera?session_id={session_id}"' in res.text


class TestVeraWebSessionIdQueryParam:
    """The canonical Vera surface accepts ?session_id= query and sets cookie."""

    def test_session_id_query_param_overrides_cookie(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)
        # Seed a session with real turns so we can confirm that the
        # canonical Vera surface actually loaded THAT session (not a new one).
        session_id = "vera-wb-query-seed"
        session_store.append_session_turn(queue, session_id, role="user", text="earlier user turn")
        session_store.append_session_turn(
            queue, session_id, role="assistant", text="earlier assistant reply"
        )

        client = TestClient(vera_app_module.app)
        # Simulate a stale cookie pointing at a DIFFERENT session — the
        # query parameter must win.
        client.cookies.set("vera_session_id", "vera-some-other-session")
        res = client.get(f"/?session_id={session_id}")
        assert res.status_code == 200
        # The returned page must load the seeded session's turns.
        assert "earlier user turn" in res.text
        # And the response must set the cookie to the query-provided id
        # so subsequent chat turns continue in the same session.
        assert res.cookies.get("vera_session_id") == session_id

    def test_empty_session_id_query_falls_back_to_cookie(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)
        session_id = "vera-wb-query-cookie"
        session_store.append_session_turn(queue, session_id, role="user", text="only turn")

        client = TestClient(vera_app_module.app)
        client.cookies.set("vera_session_id", session_id)
        # Blank query param must NOT override the cookie.
        res = client.get("/?session_id=")
        assert res.status_code == 200
        assert "only turn" in res.text

    def test_path_traversal_session_id_query_is_clamped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A traversal attempt in ``?session_id=...`` must clamp to the
        basename so the canonical Vera surface can only resolve to a
        session file under ``artifacts/vera_sessions/``."""
        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)
        client = TestClient(vera_app_module.app)
        res = client.get("/?session_id=../../etc/evil")
        assert res.status_code == 200
        # Cookie must contain the clamped basename, never the raw traversal.
        assert res.cookies.get("vera_session_id") == "evil"


class TestStaleResultHygieneAcrossRuns:
    """Even across a refresh, old success visuals must not look current."""

    def test_bare_status_page_refresh_after_run_does_not_show_old_vera_answer(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh GET /voice/status after a workbench run must NOT re-render
        the prior run's Vera answer — only the new-run-ready banner."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="old request")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        # Now do a plain GET — server must NOT re-render the prior result.
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Ack: old request" not in res.text
        assert "Current run" not in res.text
        # The "no run yet" empty state must be visible instead.
        assert "No workbench run yet" in res.text
