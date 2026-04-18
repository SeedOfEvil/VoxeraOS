"""Tests for the Voice Workbench canonical preview drafting seam.

Pins the new behavior added to the Voice Workbench:

1. Informational transcripts never create a canonical preview.
2. Action-oriented transcripts that the deterministic Vera drafter can
   recognize DO produce a real canonical preview in the same Vera
   session the workbench is writing into.
3. The preview surface on the page is sourced from canonical session
   state — it is never fabricated when canonical preview truth is
   absent.
4. No queue job is ever created by the Voice Workbench, even when a
   preview is drafted.  Submit stays explicit.
5. When drafting is attempted but the deterministic drafter returns
   ``None``, the page falls back to the truthful action-oriented
   guidance block (no fake preview claim).
6. ``Continue in Vera`` links to the same session where the preview
   actually lives.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.panel.voice_workbench_preview import (
    PREVIEW_STATUS_DRAFTED,
    PREVIEW_STATUS_ERROR,
    PREVIEW_STATUS_NO_DRAFT,
    PREVIEW_STATUS_NORMALIZE_FAILED,
    PREVIEW_STATUS_PERSIST_FAILED,
    maybe_draft_canonical_preview_for_workbench,
    summarize_canonical_preview,
)
from voxera.vera import session_store
from voxera.vera.session_store import append_session_turn
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


def _make_stt_response(*, transcript: str | None = "hello") -> STTResponse:
    return STTResponse(
        request_id="test-stt-preview",
        status=STT_STATUS_SUCCEEDED,
        transcript=transcript,
        language="en",
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


_PREVIEW_TESTID = 'data-testid="voice-workbench-preview-drafted"'
_PREVIEW_CONTINUE_TESTID = 'data-testid="voice-workbench-preview-continue"'
_ACTION_GUIDANCE_TESTID = 'data-testid="voice-workbench-action-guidance"'


class TestSeamDirect:
    """Direct tests for ``maybe_draft_canonical_preview_for_workbench``."""

    def test_empty_transcript_returns_no_draft(self, tmp_path: Path) -> None:
        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="   ",
            session_id="vera-direct-empty",
            queue_root=tmp_path,
        )
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_NO_DRAFT

    def test_action_oriented_transcript_drafts_and_persists_preview(self, tmp_path: Path) -> None:
        session_id = "vera-direct-writefile"
        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="write a note called hello.txt",
            session_id=session_id,
            queue_root=tmp_path,
        )
        assert result.ok is True
        assert result.status == PREVIEW_STATUS_DRAFTED
        assert result.draft_ref is not None
        preview = session_store.read_session_preview(tmp_path, session_id)
        assert isinstance(preview, dict)
        assert "goal" in preview
        assert isinstance(preview.get("write_file"), dict)
        assert preview["write_file"].get("path", "").endswith("hello.txt")

    def test_unrecognized_transcript_returns_no_draft(self, tmp_path: Path) -> None:
        """Deterministic drafter declines unknown shapes; seam reports no_draft."""
        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="delete the report file from notes",
            session_id="vera-direct-unknown",
            queue_root=tmp_path,
        )
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_NO_DRAFT

    def test_normalize_failure_reported_fail_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raising_normalize(_: dict[str, Any]) -> dict[str, Any]:
            raise ValueError("bad shape")

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.normalize_preview_payload",
            _raising_normalize,
        )
        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="write a note called hello.txt",
            session_id="vera-direct-normalizefail",
            queue_root=tmp_path,
        )
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_NORMALIZE_FAILED

    def test_persist_failure_reported_fail_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raising_reset(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.reset_active_preview",
            _raising_reset,
        )
        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="write a note called hello.txt",
            session_id="vera-direct-persistfail",
            queue_root=tmp_path,
        )
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_PERSIST_FAILED

    def test_session_context_read_failure_reported_fail_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a session-store read raises, the seam reports ``error`` with
        an annotation that identifies the read-failure phase.  The
        drafting / normalize / persist calls must not run."""

        def _raising_read(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
            raise RuntimeError("simulated session-store read failure")

        called = {"drafter": False, "normalize": False, "reset": False}

        def _drafter_spy(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
            called["drafter"] = True
            return {"goal": "should not run"}

        def _normalize_spy(payload: dict[str, Any]) -> dict[str, Any]:
            called["normalize"] = True
            return payload

        def _reset_spy(*_args: Any, **_kwargs: Any) -> None:
            called["reset"] = True

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.session_store.read_session_preview",
            _raising_read,
        )
        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.maybe_draft_job_payload",
            _drafter_spy,
        )
        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.normalize_preview_payload",
            _normalize_spy,
        )
        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.reset_active_preview",
            _reset_spy,
        )

        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text="write a note called hello.txt",
            session_id="vera-direct-ctxreadfail",
            queue_root=tmp_path,
        )
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_ERROR
        assert result.error is not None
        assert result.error.startswith("session_context_read_failed:")
        assert called == {"drafter": False, "normalize": False, "reset": False}


class TestSummarize:
    """summarize_canonical_preview keeps the surface bounded and truthful."""

    def test_none_preview_summarizes_to_none(self) -> None:
        assert summarize_canonical_preview(None) is None

    def test_preview_without_goal_summarizes_to_none(self) -> None:
        assert summarize_canonical_preview({"title": "no goal"}) is None

    def test_preview_with_write_file_is_surfaced(self) -> None:
        summary = summarize_canonical_preview(
            {
                "goal": "write a file called notes.txt",
                "write_file": {"path": "~/notes.txt", "mode": "overwrite"},
            }
        )
        assert summary is not None
        assert summary["goal"] == "write a file called notes.txt"
        assert summary["write_file"] == {"path": "~/notes.txt", "mode": "overwrite"}

    def test_preview_with_steps_counts_them(self) -> None:
        summary = summarize_canonical_preview(
            {
                "goal": "copy a.txt to b/",
                "steps": [{"skill_id": "files.copy"}, {"skill_id": "files.copy"}],
            }
        )
        assert summary is not None
        assert summary["step_count"] == 2


class TestInformationalRunsNeverDraftPreview:
    def test_question_form_does_not_write_preview(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-info-q"
        stt = _make_stt_response(transcript="what is the status of the queue?")
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
        assert _PREVIEW_TESTID not in res.text
        path = queue_dir / "artifacts" / "vera_sessions" / f"{session_id}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "pending_job_preview" not in data


class TestActionOrientedDraftablePreview:
    def test_write_file_transcript_drafts_real_canonical_preview(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-pv-writefile"
        stt = _make_stt_response(transcript="write a note called hello.txt")
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
        assert _PREVIEW_TESTID in res.text
        assert "Governed preview drafted" in res.text
        # The generic action-oriented guidance block must NOT render when we
        # have a real preview — the operator sees the stronger specific block.
        assert _ACTION_GUIDANCE_TESTID not in res.text

        path = queue_dir / "artifacts" / "vera_sessions" / f"{session_id}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        preview = data.get("pending_job_preview")
        assert isinstance(preview, dict)
        assert isinstance(preview.get("write_file"), dict)
        assert preview["write_file"].get("path", "").endswith("hello.txt")

    def test_preview_continue_link_points_to_same_session(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        session_id = "vera-wb-pv-continue"
        stt = _make_stt_response(transcript="write a note called hello.txt")
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
        assert _PREVIEW_CONTINUE_TESTID in res.text
        assert f"?session_id={session_id}" in res.text


class TestPreviewBlockTruthfulness:
    """Preview block surfaces canonical truth, never fabrication."""

    def test_preview_block_wording_does_not_claim_submission(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="please copy report.txt into receipts")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": "vera-wb-pv-truth",
                },
            )
        assert res.status_code == 200
        assert _PREVIEW_TESTID in res.text
        start = res.text.index(_PREVIEW_TESTID)
        end = res.text.index("</div>", res.text.index("</div>", start) + 1)
        block = res.text[start:end].lower()
        # Preserve the trust-model boundary: no submission or execution claims.
        assert "has been submitted" not in block
        assert "has been queued" not in block
        assert "job submitted" not in block
        assert "executed successfully" not in block
        assert "ready to submit" not in block
        # Preserve the positive, truthful language the mission requires.
        assert "governed preview drafted" in block or "governed preview has been drafted" in block

    def test_preview_drafted_writes_no_queue_jobs(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="write a note called draft.txt")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": "vera-wb-pv-noqueue",
                },
            )
        assert res.status_code == 200
        assert _PREVIEW_TESTID in res.text
        for bucket in ("inbox", "pending", "running", "done", "failed", "canceled", "approvals"):
            bucket_dir = queue_dir / bucket
            if not bucket_dir.exists():
                continue
            job_files = list(bucket_dir.rglob("*.json"))
            assert job_files == [], f"unexpected job file(s) in {bucket}: {job_files}"


class TestDraftingFailureFallsBackTruthfully:
    def test_no_draft_falls_back_to_action_guidance(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Action-oriented transcript the deterministic drafter can't
        recognize must not render a fabricated preview block; the page
        falls back to the existing truthful guidance block."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-pv-nodraft"
        stt = _make_stt_response(transcript="delete the stale artifact folder")
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
        assert _PREVIEW_TESTID not in res.text
        assert _ACTION_GUIDANCE_TESTID in res.text
        path = queue_dir / "artifacts" / "vera_sessions" / f"{session_id}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "pending_job_preview" not in data


class TestPreviewAttributionIsRunScoped:
    """Hardening: the 'Governed preview drafted' block claims *this run*
    drafted a preview.  When the seam reports ``ok=False`` — even if an
    unrelated canonical preview was already sitting on the session from a
    prior canonical Vera chat turn — the workbench must NOT surface
    the preview block under its own attribution.

    The prior canonical preview is still on disk (it belongs to the
    session, not to this run); the operator reaches it via ``Continue in
    Vera``, which lands on the same session.  But this voice surface
    does not claim agency it does not have.
    """

    def test_no_draft_with_prior_canonical_preview_still_suppresses_preview_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-pv-prior-preview-nodraft"

        # Simulate a canonical Vera chat turn that drafted a preview on
        # this session BEFORE the voice run starts.  This write goes
        # directly through the canonical preview-ownership helper so the
        # session file mirrors real chat-drafted state.
        from voxera.vera.preview_ownership import reset_active_preview

        # NOTE: this path is fully containerized under the tmp queue_dir
        # via the _panel_env fixture's Path.home monkeypatch; it never
        # touches the real user home.
        reset_active_preview(
            queue_dir,
            session_id,
            {
                "goal": "write a file called report.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/report.txt",
                    "content": "x",
                    "mode": "overwrite",
                },
            },
            draft_ref="~/VoxeraOS/notes/report.txt",
        )
        pre_preview = session_store.read_session_preview(queue_dir, session_id)
        assert isinstance(pre_preview, dict)
        assert pre_preview["write_file"]["path"].endswith("report.txt")

        # The voice run is action-oriented ("delete the stale artifact
        # folder") but the deterministic drafter declines — seam reports
        # ``no_draft``.  The preview block must NOT render, because this
        # run did not draft anything.
        stt = _make_stt_response(transcript="delete the stale artifact folder")
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

        # The "Governed preview drafted" block must be absent — this run
        # did not draft.  The truthful action-guidance fallback renders
        # instead, pointing the operator to canonical Vera.
        assert _PREVIEW_TESTID not in res.text
        assert _ACTION_GUIDANCE_TESTID in res.text
        # The badge copy that announces preview_ready must not leak into
        # the workbench section under this run's attribution.
        assert "Governed preview drafted" not in res.text

        # Canonical preview state on disk is preserved (untouched by the
        # seam's fail-closed no_draft return).  The operator following
        # "Continue in Vera" will see the real preview in canonical Vera.
        post_preview = session_store.read_session_preview(queue_dir, session_id)
        assert post_preview == pre_preview

    def test_persist_failure_with_prior_preview_does_not_claim_drafted(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``reset_active_preview`` raises on a run that otherwise
        would have drafted a recognized shape, the seam returns
        ``persist_failed`` and the route must not surface any prior
        canonical preview as though this run drafted it."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        session_id = "vera-wb-pv-prior-preview-persistfail"

        from voxera.vera.preview_ownership import reset_active_preview as real_reset

        real_reset(
            queue_dir,
            session_id,
            {
                "goal": "write a file called earlier.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/earlier.txt",
                    "content": "y",
                    "mode": "overwrite",
                },
            },
            draft_ref="~/VoxeraOS/notes/earlier.txt",
        )

        def _raising_reset(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated persist failure")

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.reset_active_preview",
            _raising_reset,
        )

        stt = _make_stt_response(transcript="write a note called newer.txt")
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
        assert _PREVIEW_TESTID not in res.text
        assert "Governed preview drafted" not in res.text
        # Action-guidance fallback renders because no preview was drafted.
        assert _ACTION_GUIDANCE_TESTID in res.text


class TestSeamNeverImportsSubmissionHelpers:
    """Structural invariant: the seam module MUST NOT import or reference
    any queue-submission helper.  The trust model is load-bearing and the
    module docstring claims the seam only calls ``reset_active_preview``;
    if a future refactor adds a submission import the invariant silently
    breaks.  This test enforces the claim at test time by reading the
    seam's source as text and asserting forbidden symbols never appear.
    """

    _FORBIDDEN_SYMBOLS = (
        "submit_preview",
        "submit_active_preview_for_session",
        "submit_automation_preview",
        "add_inbox_payload",
        "inbox",
    )

    def test_seam_source_contains_no_submission_symbols(self) -> None:
        import voxera.panel.voice_workbench_preview as seam_module

        source_path = Path(seam_module.__file__)
        source = source_path.read_text(encoding="utf-8")
        for symbol in self._FORBIDDEN_SYMBOLS:
            assert symbol not in source, (
                f"Forbidden submission symbol {symbol!r} found in {source_path}. "
                "The Voice Workbench seam must not import or reference any "
                "queue-submission helper — it only writes the active preview "
                "via reset_active_preview."
            )


class TestSeamObservesPersistedTranscriptTurn:
    """Ordering invariant: the seam's docstring says the transcript turn
    must already be persisted as a ``voice_transcript``-origin user turn
    BEFORE this helper is called, because the drafter reads
    ``recent_user_messages`` from canonical session state.  The route
    today satisfies this by running the Vera step (which persists the
    turn) before the preview seam, but a future refactor could silently
    reorder that.  This test pins the load-bearing ordering by spying on
    ``maybe_draft_job_payload`` and asserting the drafter observes the
    persisted turn text via ``recent_user_messages``."""

    def test_drafter_sees_persisted_user_turn_via_recent_user_messages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_id = "vera-direct-ordering"
        transcript = "write a note called hello.txt"

        # Persist the voice_transcript user turn BEFORE calling the seam
        # — this mirrors the route's ordering (Vera step, which persists
        # the turn, runs before the preview seam).
        append_session_turn(
            tmp_path,
            session_id,
            role="user",
            text=transcript,
            input_origin="voice_transcript",
        )

        captured: dict[str, Any] = {}

        def _drafter_spy(
            transcript_text: str,
            *,
            active_preview: Any = None,
            recent_user_messages: list[str] | None = None,
            recent_assistant_messages: list[str] | None = None,
            recent_assistant_artifacts: Any = None,
            investigation_context: Any = None,
            session_context: Any = None,
        ) -> dict[str, Any] | None:
            captured["transcript_text"] = transcript_text
            captured["recent_user_messages"] = list(recent_user_messages or [])
            captured["recent_assistant_messages"] = list(recent_assistant_messages or [])
            return None

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_preview.maybe_draft_job_payload",
            _drafter_spy,
        )

        result = maybe_draft_canonical_preview_for_workbench(
            transcript_text=transcript,
            session_id=session_id,
            queue_root=tmp_path,
        )

        # Drafter declined (spy returned None) — status is no_draft, but
        # the important assertion is that the spy OBSERVED the persisted
        # turn via recent_user_messages.
        assert result.ok is False
        assert result.status == PREVIEW_STATUS_NO_DRAFT
        assert captured["transcript_text"] == transcript
        assert transcript in captured["recent_user_messages"], (
            "Drafter did not observe the persisted voice_transcript turn via "
            "recent_user_messages — the seam is running before the turn is "
            "persisted, violating the ordering invariant documented in the "
            "seam's module docstring."
        )
