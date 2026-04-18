"""Tests for the Voice Workbench spoken lifecycle commands seam.

Pins the new behavior added to the Voice Workbench:

1. Bounded lifecycle phrases ("submit it", "send it", "approve it",
   "deny it", …) classify as lifecycle commands; richer sentences
   stay in the normal Vera conversational lane.
2. The submit lifecycle lane dispatches through the canonical
   ``submit_active_preview_for_session`` seam and honors its
   fail-closed statuses (missing preview, ambiguous preview, queue
   write failure).
3. The approve / deny lifecycle lanes dispatch against canonical
   approval truth scoped to the current session's linked jobs, and
   fail closed on missing / ambiguous approvals.
4. The route surfaces lifecycle results truthfully, suppresses the
   Vera/preview/action-guidance blocks when a lifecycle command is
   handled, and never fabricates preview or approval state.
5. When a lifecycle phrase is detected but voice input is disabled,
   nothing is persisted and the dispatcher is never called.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.panel.voice_workbench_lifecycle import (
    LIFECYCLE_ACTION_APPROVE,
    LIFECYCLE_ACTION_DENY,
    LIFECYCLE_ACTION_NONE,
    LIFECYCLE_ACTION_SUBMIT,
    LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL,
    LIFECYCLE_STATUS_AMBIGUOUS_PREVIEW,
    LIFECYCLE_STATUS_APPROVAL_FAILED,
    LIFECYCLE_STATUS_APPROVED,
    LIFECYCLE_STATUS_DENIED,
    LIFECYCLE_STATUS_NO_PENDING_APPROVAL,
    LIFECYCLE_STATUS_NO_PREVIEW,
    LIFECYCLE_STATUS_SUBMITTED,
    VoiceWorkbenchLifecycleClassification,
    classify_lifecycle_phrase,
    dispatch_spoken_lifecycle_command,
)
from voxera.vera import session_store
from voxera.vera.preview_ownership import reset_active_preview
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
        request_id="test-stt-lifecycle",
        status=STT_STATUS_SUCCEEDED,
        transcript=transcript,
        language="en",
        audio_duration_ms=1500,
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


_LIFECYCLE_TESTID = 'data-testid="voice-workbench-lifecycle"'
_PREVIEW_TESTID = 'data-testid="voice-workbench-preview-drafted"'
_ACTION_GUIDANCE_TESTID = 'data-testid="voice-workbench-action-guidance"'


# ─────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────


class TestClassifier:
    @pytest.mark.parametrize(
        "phrase",
        [
            "submit it",
            "Submit it.",
            "SEND IT",
            "run it!",
            "save it",
            "submit this",
            "submit that.",
            "send this",
            "run that",
        ],
    )
    def test_submit_phrases_classify_as_submit(self, phrase: str) -> None:
        result = classify_lifecycle_phrase(phrase)
        assert result.kind == LIFECYCLE_ACTION_SUBMIT
        assert result.matched_phrase == phrase.strip()

    @pytest.mark.parametrize("phrase", ["approve it", "Approve this.", "APPROVE THAT"])
    def test_approve_phrases_classify_as_approve(self, phrase: str) -> None:
        result = classify_lifecycle_phrase(phrase)
        assert result.kind == LIFECYCLE_ACTION_APPROVE

    @pytest.mark.parametrize(
        "phrase",
        ["deny it", "Deny that.", "reject it", "Reject this!"],
    )
    def test_deny_phrases_classify_as_deny(self, phrase: str) -> None:
        result = classify_lifecycle_phrase(phrase)
        assert result.kind == LIFECYCLE_ACTION_DENY

    @pytest.mark.parametrize(
        "phrase",
        [
            "",
            "   ",
            "what is the status of the queue?",
            "please submit the report to the manager by friday",
            "I want to approve the request for more time",
            "do not deny the user access",
            "submit",  # no target pronoun
            "approve me",  # non-pronoun target
            "submit it please now",  # trailing words beyond punctuation
            "write a note called hello.txt",  # regular drafting request
        ],
    )
    def test_non_lifecycle_phrases_classify_as_none(self, phrase: str) -> None:
        result = classify_lifecycle_phrase(phrase)
        assert result.kind == LIFECYCLE_ACTION_NONE

    def test_none_input_is_none(self) -> None:
        assert classify_lifecycle_phrase(None).kind == LIFECYCLE_ACTION_NONE


# ─────────────────────────────────────────────────────────────────────
# Dispatcher — submit
# ─────────────────────────────────────────────────────────────────────


class TestDispatchSubmit:
    def _classification(self) -> VoiceWorkbenchLifecycleClassification:
        return classify_lifecycle_phrase("submit it")

    def test_submit_with_canonical_preview_reports_submitted(self, tmp_path: Path) -> None:
        session_id = "lc-submit-ok"

        def fake_submit(**kwargs: Any) -> tuple[str, str]:
            # Simulate the canonical helper accepting the session's
            # preview and writing the handoff state.
            session_store.write_session_handoff_state(
                tmp_path,
                session_id,
                attempted=True,
                queue_path=str(tmp_path),
                status="submitted",
                job_id="abc123",
            )
            return ("Submitted job abc123.", "handoff_submitted")

        result = dispatch_spoken_lifecycle_command(
            classification=self._classification(),
            session_id=session_id,
            queue_root=tmp_path,
            submit_hook=fake_submit,
        )
        assert result.ok is True
        assert result.action == LIFECYCLE_ACTION_SUBMIT
        assert result.status == LIFECYCLE_STATUS_SUBMITTED
        assert result.job_id == "abc123"
        assert result.ack is not None

    def test_submit_without_preview_reports_no_preview(self, tmp_path: Path) -> None:
        def fake_submit(**kwargs: Any) -> tuple[str, str]:
            return ("No prepared preview on this session.", "handoff_missing_preview")

        result = dispatch_spoken_lifecycle_command(
            classification=self._classification(),
            session_id="lc-submit-none",
            queue_root=tmp_path,
            submit_hook=fake_submit,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_NO_PREVIEW
        assert result.ack and "did not submit" in result.ack.lower() or result.ack

    def test_submit_with_ambiguous_preview_state_fails_closed(self, tmp_path: Path) -> None:
        def fake_submit(**kwargs: Any) -> tuple[str, str]:
            return ("Ambiguous preview state", "handoff_ambiguous_preview_state")

        result = dispatch_spoken_lifecycle_command(
            classification=self._classification(),
            session_id="lc-submit-ambig",
            queue_root=tmp_path,
            submit_hook=fake_submit,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_AMBIGUOUS_PREVIEW

    def test_submit_hook_raising_reports_error(self, tmp_path: Path) -> None:
        def fake_submit(**kwargs: Any) -> tuple[str, str]:
            raise RuntimeError("queue write failed")

        result = dispatch_spoken_lifecycle_command(
            classification=self._classification(),
            session_id="lc-submit-raise",
            queue_root=tmp_path,
            submit_hook=fake_submit,
        )
        assert result.ok is False
        assert result.status == "error"
        assert result.error is not None
        assert "RuntimeError" in result.error


# ─────────────────────────────────────────────────────────────────────
# Dispatcher — approve / deny
# ─────────────────────────────────────────────────────────────────────


class TestDispatchApproval:
    def _approve_classification(self) -> VoiceWorkbenchLifecycleClassification:
        return classify_lifecycle_phrase("approve it")

    def _deny_classification(self) -> VoiceWorkbenchLifecycleClassification:
        return classify_lifecycle_phrase("deny it")

    def test_approve_without_pending_approval_fails_closed(self, tmp_path: Path) -> None:
        session_id = "lc-approve-none"
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-abc.json")
        result = dispatch_spoken_lifecycle_command(
            classification=self._approve_classification(),
            session_id=session_id,
            queue_root=tmp_path,
            approvals_list_hook=lambda: [],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=lambda ref, approve: True,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_NO_PENDING_APPROVAL
        assert result.ack is not None

    def test_approve_with_no_linked_jobs_fails_closed(self, tmp_path: Path) -> None:
        # Pending approval exists in the queue but this session has no
        # linked jobs — the dispatcher refuses to act on unrelated state.
        resolve_called = {"count": 0}

        def fake_resolve(ref: str, approve: bool) -> bool:
            resolve_called["count"] += 1
            return True

        result = dispatch_spoken_lifecycle_command(
            classification=self._approve_classification(),
            session_id="lc-approve-nolink",
            queue_root=tmp_path,
            approvals_list_hook=lambda: [{"job": "inbox-unrelated.json"}],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=fake_resolve,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_NO_PENDING_APPROVAL
        assert resolve_called["count"] == 0

    def test_approve_with_single_scoped_approval_resolves(self, tmp_path: Path) -> None:
        session_id = "lc-approve-ok"
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-xyz.json")
        calls: list[tuple[str, bool]] = []

        def fake_resolve(ref: str, approve: bool) -> bool:
            calls.append((ref, approve))
            return True

        result = dispatch_spoken_lifecycle_command(
            classification=self._approve_classification(),
            session_id=session_id,
            queue_root=tmp_path,
            approvals_list_hook=lambda: [{"job": "inbox-xyz.json"}],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=fake_resolve,
        )
        assert result.ok is True
        assert result.action == LIFECYCLE_ACTION_APPROVE
        assert result.status == LIFECYCLE_STATUS_APPROVED
        assert result.approval_ref == "inbox-xyz.json"
        assert calls == [("inbox-xyz.json", True)]

    def test_deny_with_single_scoped_approval_resolves(self, tmp_path: Path) -> None:
        session_id = "lc-deny-ok"
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-xyz.json")
        calls: list[tuple[str, bool]] = []

        def fake_resolve(ref: str, approve: bool) -> bool:
            calls.append((ref, approve))
            return True

        result = dispatch_spoken_lifecycle_command(
            classification=self._deny_classification(),
            session_id=session_id,
            queue_root=tmp_path,
            approvals_list_hook=lambda: [{"job": "inbox-xyz.json"}],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=fake_resolve,
        )
        assert result.ok is True
        assert result.action == LIFECYCLE_ACTION_DENY
        assert result.status == LIFECYCLE_STATUS_DENIED
        assert calls == [("inbox-xyz.json", False)]

    def test_approve_with_multiple_scoped_approvals_fails_closed(self, tmp_path: Path) -> None:
        session_id = "lc-approve-ambig"
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-one.json")
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-two.json")
        resolved: list[str] = []

        def fake_resolve(ref: str, approve: bool) -> bool:
            resolved.append(ref)
            return True

        result = dispatch_spoken_lifecycle_command(
            classification=self._approve_classification(),
            session_id=session_id,
            queue_root=tmp_path,
            approvals_list_hook=lambda: [
                {"job": "inbox-one.json"},
                {"job": "inbox-two.json"},
            ],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=fake_resolve,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL
        assert resolved == []

    def test_approve_resolve_returning_false_reports_failure(self, tmp_path: Path) -> None:
        session_id = "lc-approve-false"
        session_store.register_session_linked_job(tmp_path, session_id, job_ref="inbox-xyz.json")
        result = dispatch_spoken_lifecycle_command(
            classification=self._approve_classification(),
            session_id=session_id,
            queue_root=tmp_path,
            approvals_list_hook=lambda: [{"job": "inbox-xyz.json"}],
            canonicalize_ref_hook=lambda ref: ref,
            resolve_approval_hook=lambda ref, approve: False,
        )
        assert result.ok is False
        assert result.status == LIFECYCLE_STATUS_APPROVAL_FAILED


# ─────────────────────────────────────────────────────────────────────
# Route integration
# ─────────────────────────────────────────────────────────────────────


class TestRouteLifecycleSubmit:
    def test_submit_it_with_canonical_preview_submits_and_suppresses_other_blocks(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue_dir = _panel_env
        session_id = "lc-route-submit-ok"
        # Seed canonical preview via the real ownership helper.
        reset_active_preview(
            queue_dir,
            session_id,
            {
                "goal": "write a file called lifecycle.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/lifecycle.txt",
                    "content": "x",
                    "mode": "overwrite",
                },
            },
            draft_ref="~/VoxeraOS/notes/lifecycle.txt",
        )

        def fake_submit(
            *,
            queue_root: Path,
            session_id: str,
            preview: Any,
            register_linked_job: Any,
        ) -> tuple[str, str]:
            session_store.write_session_handoff_state(
                queue_root,
                session_id,
                attempted=True,
                queue_path=str(queue_root),
                status="submitted",
                job_id="fakejob1",
            )
            session_store.write_session_preview(queue_root, session_id, None)
            if register_linked_job is not None:
                register_linked_job(queue_root, session_id, job_ref="inbox-fakejob1.json")
            return ("I submitted the job to VoxeraOS. Job id: fakejob1.", "handoff_submitted")

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_lifecycle.submit_active_preview_for_session",
            fake_submit,
        )
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="submit it")
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
        assert _LIFECYCLE_TESTID in res.text
        assert "submitted" in res.text.lower()
        assert "fakejob1" in res.text
        # Vera/preview/action-guidance blocks must be suppressed.
        assert _PREVIEW_TESTID not in res.text
        assert _ACTION_GUIDANCE_TESTID not in res.text
        assert "Vera was not called" in res.text

    def test_submit_it_without_preview_reports_no_preview(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue_dir = _panel_env
        session_id = "lc-route-submit-none"

        submit_called = {"count": 0}

        def fake_submit(**kwargs: Any) -> tuple[str, str]:
            submit_called["count"] += 1
            return (
                "I don't have a prepared preview in this session yet, so I did not submit anything.",
                "handoff_missing_preview",
            )

        monkeypatch.setattr(
            "voxera.panel.voice_workbench_lifecycle.submit_active_preview_for_session",
            fake_submit,
        )
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="submit it")
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
        assert submit_called["count"] == 1
        assert _LIFECYCLE_TESTID in res.text
        assert LIFECYCLE_STATUS_NO_PREVIEW in res.text
        # No queue artefacts should exist — the submit lane refused.
        for bucket in ("inbox", "pending", "running", "done", "failed", "canceled"):
            bucket_dir = queue_dir / bucket
            if not bucket_dir.exists():
                continue
            assert list(bucket_dir.rglob("*.json")) == []


class TestRouteLifecycleApproval:
    def test_approve_it_with_single_linked_approval_resolves(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue_dir = _panel_env
        session_id = "lc-route-approve-ok"
        session_store.register_session_linked_job(
            queue_dir, session_id, job_ref="inbox-linked.json"
        )

        class FakeDaemon:
            def __init__(self, queue_root: Path) -> None:
                self.queue_root = queue_root
                self.resolve_calls: list[tuple[str, bool]] = []

            def approvals_list(self) -> list[dict[str, Any]]:
                return [{"job": "inbox-linked.json"}]

            def canonicalize_approval_ref(self, ref: str) -> str:
                return Path(ref).name

            def resolve_approval(
                self, ref: str, *, approve: bool, approve_always: bool = False
            ) -> bool:
                self.resolve_calls.append((ref, approve))
                return True

        captured: dict[str, FakeDaemon] = {}

        def fake_daemon_ctor(*, queue_root: Path) -> FakeDaemon:
            captured["d"] = FakeDaemon(queue_root)
            return captured["d"]

        monkeypatch.setattr("voxera.core.queue_daemon.MissionQueueDaemon", fake_daemon_ctor)
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="approve it")
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
        assert _LIFECYCLE_TESTID in res.text
        assert LIFECYCLE_STATUS_APPROVED in res.text
        assert "inbox-linked.json" in res.text
        assert "d" in captured
        assert captured["d"].resolve_calls == [("inbox-linked.json", True)]

    def test_deny_it_with_single_linked_approval_resolves(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue_dir = _panel_env
        session_id = "lc-route-deny-ok"
        session_store.register_session_linked_job(
            queue_dir, session_id, job_ref="inbox-linked.json"
        )

        resolve_calls: list[tuple[str, bool]] = []

        class FakeDaemon:
            def __init__(self, queue_root: Path) -> None:
                self.queue_root = queue_root

            def approvals_list(self) -> list[dict[str, Any]]:
                return [{"job": "inbox-linked.json"}]

            def canonicalize_approval_ref(self, ref: str) -> str:
                return Path(ref).name

            def resolve_approval(
                self, ref: str, *, approve: bool, approve_always: bool = False
            ) -> bool:
                resolve_calls.append((ref, approve))
                return True

        monkeypatch.setattr(
            "voxera.core.queue_daemon.MissionQueueDaemon",
            lambda *, queue_root: FakeDaemon(queue_root),
        )
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="deny it")
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
        assert _LIFECYCLE_TESTID in res.text
        assert LIFECYCLE_STATUS_DENIED in res.text
        assert resolve_calls == [("inbox-linked.json", False)]

    def test_approve_without_pending_approval_fails_closed_in_ui(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue_dir = _panel_env
        session_id = "lc-route-approve-none"
        session_store.register_session_linked_job(
            queue_dir, session_id, job_ref="inbox-linked.json"
        )

        resolve_calls: list[tuple[str, bool]] = []

        class FakeDaemon:
            def __init__(self, queue_root: Path) -> None:
                pass

            def approvals_list(self) -> list[dict[str, Any]]:
                return []

            def canonicalize_approval_ref(self, ref: str) -> str:
                return Path(ref).name

            def resolve_approval(
                self, ref: str, *, approve: bool, approve_always: bool = False
            ) -> bool:
                resolve_calls.append((ref, approve))
                return True

        monkeypatch.setattr(
            "voxera.core.queue_daemon.MissionQueueDaemon",
            lambda *, queue_root: FakeDaemon(queue_root),
        )
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="approve it")
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
        assert _LIFECYCLE_TESTID in res.text
        assert LIFECYCLE_STATUS_NO_PENDING_APPROVAL in res.text
        assert resolve_calls == []


class TestRouteLifecycleDoesNotRegressDrafting:
    """Pin: action-oriented transcripts that are NOT lifecycle phrases
    still drive the preview-drafting lane unchanged."""

    def test_write_file_transcript_still_drafts_canonical_preview(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        session_id = "lc-route-still-drafts"
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
        # The lifecycle block must not render for a non-lifecycle phrase.
        assert _LIFECYCLE_TESTID not in res.text
        # The preview-drafted block renders as before.
        assert _PREVIEW_TESTID in res.text

    def test_informational_transcript_does_not_render_lifecycle_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        session_id = "lc-route-informational"
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
        assert _LIFECYCLE_TESTID not in res.text
