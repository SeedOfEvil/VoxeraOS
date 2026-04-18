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


# The canonical Vera web app defaults to ``http://127.0.0.1:8790`` (see
# ``VoxeraConfig.vera_web_base_url``).  The panel runs on a separate
# uvicorn process so continuation links must be absolute against this
# base URL — a relative ``/vera`` would 404 on the panel host.
_DEFAULT_VERA_WEB_BASE_URL = "http://127.0.0.1:8790"


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
        assert f"{_DEFAULT_VERA_WEB_BASE_URL}/?session_id={session_id}" in res.text


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
        assert f"{_DEFAULT_VERA_WEB_BASE_URL}/?session_id={session_id}" in res.text
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
        # The clamped component is "evil" — never the original traversal
        # in any continuation URL.  (The raw workbench_session_id may still
        # appear as display text in the banner and as a round-tripped
        # hidden form value — the safety contract is that the
        # ``Continue in Vera`` HREF is clamped to the basename.)
        assert f"{_DEFAULT_VERA_WEB_BASE_URL}/?session_id=evil" in res.text
        # No continuation-URL query string ever carries the traversal.
        assert "?session_id=../../etc/evil" not in res.text
        assert "?session_id=../" not in res.text


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
        # At least one anchor pointing at the canonical Vera web app with
        # ``?session_id=<id>`` must appear on the page (banner). The
        # handoff block links to the same target.
        assert f'href="{_DEFAULT_VERA_WEB_BASE_URL}/?session_id={session_id}"' in res.text


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


class TestSessionCookiePersistence:
    """The voice surface must persist the resolved Vera session id so the
    banner does not flip between page loads and so the canonical Vera
    surface sees the same session the operator was just working in."""

    def test_voice_status_sets_vera_session_cookie_on_first_visit(self, _panel_env: Path) -> None:
        """Arriving on /voice/status without a cookie mints a fresh session id
        for display.  Without persisting it, a refresh would mint a DIFFERENT
        id and the continuity banner would jump — the exact problem the polish
        is meant to fix.  The response must write the minted id back so the
        next request (to /voice/status, /vera, or the canonical Vera web app)
        sees the same session id."""
        client = TestClient(panel_module.app)
        # No pre-existing cookie.
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        persisted = res.cookies.get("vera_session_id")
        assert persisted, "voice route must persist the resolved vera_session_id"
        assert persisted.startswith("vera-")  # new_session_id() format
        # And the banner must surface exactly that id.
        assert persisted in res.text

    def test_voice_status_preserves_existing_vera_session_cookie(self, _panel_env: Path) -> None:
        """When the operator already has a ``vera_session_id`` cookie, the
        response must NOT rewrite it to a different id — the voice surface
        is adopting the canonical session, not minting a competing one."""
        client = TestClient(panel_module.app)
        existing = "vera-existing-cookie-session"
        client.cookies.set("vera_session_id", existing)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # If the response set the cookie at all, it must match what came in.
        rewritten = res.cookies.get("vera_session_id")
        if rewritten is not None:
            assert rewritten == existing

    def test_workbench_run_persists_run_session_cookie(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a POST /voice/workbench/run, the response must carry the
        run's session id back as a cookie so a subsequent GET — whether on
        this surface or on canonical Vera — resolves to the same session."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="persist this")
        session_id = "vera-wb-cty-persist"
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
        assert res.cookies.get("vera_session_id") == session_id


class TestFailurePathContinuityFraming:
    """Failure-path runs must still honour the continuity contract: banner
    renders, current-run meta is truthful, and the governed-handoff block
    continues to read ``not attempted`` with no fabricated execution."""

    def test_stt_empty_transcript_failure_keeps_continuity_banner_and_truth_model(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        # Adapter pathology: status=succeeded but no transcript.
        stt = _make_stt_response(transcript=None)
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
        # Continuity banner still present.
        assert 'data-testid="voice-workbench-continuity"' in res.text
        # Current-run meta still present — so the operator can tell which
        # failure corresponds to which submit.
        assert 'data-testid="voice-workbench-run-meta"' in res.text
        assert "Current run" in res.text
        # Vera was not called because no real transcript — truthful wording.
        assert "upstream transcription did not produce a real transcript" in res.text
        # Governed handoff still truthfully ``not attempted``.
        assert "not attempted" in res.text
        assert "No queue preview was drafted" in res.text
        # Must never imply success on the failure card.
        failure_card_start = res.text.index("Transcript")
        failure_card_end = res.text.index("</div>", failure_card_start)
        # The transcript card should carry a failure badge, never ``transcribed``.
        assert "transcribed" not in res.text[failure_card_start:failure_card_end]


class TestPluralizationEdgeCases:
    """Pin pluralization of turn counts in the banner so a single-turn
    session reads ``1 turn recorded``, not ``1 turns recorded``."""

    def test_banner_reads_singular_turn_for_one_turn_session(self, _panel_env: Path) -> None:
        queue_dir = _panel_env
        session_id = "vera-wb-cty-singular"
        session_store.append_session_turn(queue_dir, session_id, role="user", text="only one")
        client = TestClient(panel_module.app)
        client.cookies.set("vera_session_id", session_id)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "1 turn recorded" in res.text
        assert "1 turns recorded" not in res.text


class TestContinueInVeraUrlBuilder:
    """Pin ``_continue_in_vera_url`` so the continuation link always points at
    the canonical Vera web base URL (default ``http://127.0.0.1:8790``),
    not a relative ``/vera`` path that 404s on the panel host.

    PR #347 manual validation caught a runtime 404: the panel process
    (default 8844) does not register ``/vera``; the canonical Vera surface
    runs as a separate uvicorn process at ``vera_web_base_url`` and that
    is the only surface where ``?session_id=<id>`` handoff actually works.
    """

    def test_builder_produces_absolute_url_for_valid_session_id(self) -> None:
        from voxera.panel.routes_voice import _continue_in_vera_url

        url = _continue_in_vera_url("abc123", "http://127.0.0.1:8790")
        assert url == "http://127.0.0.1:8790/?session_id=abc123"
        # Never a relative link that would 404 on the panel host.
        assert not url.startswith("/vera")

    def test_builder_honours_custom_base_url(self) -> None:
        from voxera.panel.routes_voice import _continue_in_vera_url

        url = _continue_in_vera_url("s1", "https://vera.example.com:9000")
        assert url == "https://vera.example.com:9000/?session_id=s1"

    def test_builder_strips_trailing_slash_on_base_url(self) -> None:
        from voxera.panel.routes_voice import _continue_in_vera_url

        url = _continue_in_vera_url("s1", "http://127.0.0.1:8790/")
        assert url == "http://127.0.0.1:8790/?session_id=s1"
        # Never doubles the slash before the query.
        assert "//?session_id" not in url

    def test_builder_clamps_path_traversal_session_ids(self) -> None:
        from voxera.panel.routes_voice import _continue_in_vera_url

        url = _continue_in_vera_url("../../etc/evil", "http://127.0.0.1:8790")
        assert url == "http://127.0.0.1:8790/?session_id=evil"
        assert "../../etc/evil" not in url

    def test_builder_empty_session_id_drops_query(self) -> None:
        from voxera.panel.routes_voice import _continue_in_vera_url

        # Blank id → base landing page (canonical Vera falls back to cookie
        # or mints a new session).  No ``?session_id=`` query.
        assert _continue_in_vera_url("", "http://127.0.0.1:8790") == "http://127.0.0.1:8790/"
        assert _continue_in_vera_url(".", "http://127.0.0.1:8790") == "http://127.0.0.1:8790/"

    def test_builder_rejects_unusable_base_url_and_falls_back_to_default(self) -> None:
        """An empty or non-http(s) base URL must collapse to the default
        canonical Vera base URL so the link never degrades to a broken
        relative ``/vera`` path."""
        from voxera.panel.routes_voice import _continue_in_vera_url

        # Empty → default.
        assert _continue_in_vera_url("s1", "") == "http://127.0.0.1:8790/?session_id=s1"
        # Suspicious scheme → default.
        assert (
            _continue_in_vera_url("s1", "javascript:alert(1)")
            == "http://127.0.0.1:8790/?session_id=s1"
        )
        # Relative path → default.
        assert _continue_in_vera_url("s1", "/vera") == "http://127.0.0.1:8790/?session_id=s1"


class TestContinuationUrlRespectsConfiguredBaseUrl:
    """End-to-end: a non-default ``vera_web_base_url`` setting flows all the
    way into the rendered ``Continue in Vera`` link.  This pins the
    deployment model where the canonical Vera surface lives on a
    non-default host/port."""

    def test_voice_status_uses_configured_base_url(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_VERA_WEB_BASE_URL", "https://vera.example.test:9443")
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "https://vera.example.test:9443/?session_id=" in res.text
        # No stray relative link that would 404 on the panel host.
        assert 'href="/vera?session_id=' not in res.text

    def test_workbench_run_uses_configured_base_url(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_VERA_WEB_BASE_URL", "https://vera.example.test:9443")
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        session_id = "vera-wb-cty-base-url"
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
        assert f'href="https://vera.example.test:9443/?session_id={session_id}"' in res.text
        # Never the broken relative link.
        assert f'href="/vera?session_id={session_id}"' not in res.text


class TestNoBrokenRelativeVeraLink:
    """Regression: the rendered page must never emit a relative ``/vera``
    link for the continuation affordance under the split-process
    deployment model, since ``/vera`` is not mounted on the panel host."""

    def test_voice_status_never_emits_relative_vera_continuation_link(
        self, _panel_env: Path
    ) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # Relative href="/vera..." would 404 on the panel host.
        assert 'href="/vera"' not in res.text
        assert 'href="/vera?' not in res.text

    def test_workbench_run_never_emits_relative_vera_continuation_link(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="ping")
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
        assert 'href="/vera"' not in res.text
        assert 'href="/vera?' not in res.text
