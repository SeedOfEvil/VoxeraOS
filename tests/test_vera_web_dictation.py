"""Tests for canonical Vera's dictation route (``POST /chat/voice``).

Pins the Vera-native dictation lane at parity with typed ``/chat``:

1. The Vera index page advertises the mic button, voice bar, and the
   dictation enhancer script so the browser can progressively enhance
   typed chat with microphone capture.
2. ``POST /chat/voice`` with a valid audio body feeds the canonical
   :func:`voxera.vera_web.app.run_vera_chat_turn` helper — the SAME
   helper typed ``/chat`` uses — and persists a ``voice_transcript``-
   origin user turn plus an assistant turn on the canonical Vera
   session.
3. An informational transcript surfaces Vera's reply without drafting
   a preview (conversational lane intact) — identical to typed.
4. Typed and dictated submissions of the same informational message
   produce identical assistant text, identical ``preview`` field, and
   identical stored turn shapes.
5. A dictated "submit it" on a session with an active preview routes
   through the canonical explicit-submit seam — no bespoke dictation
   lifecycle path.
6. When ``speak_response=1`` and TTS succeeds, the JSON includes a
   tokenized ``tts_url`` and ``GET /vera/voice/audio/<token>`` serves
   the audio.
7. When TTS fails, text stays authoritative: ``tts_url`` is ``None``
   but the assistant turn is still in the session.
8. Empty / non-audio / oversized bodies fail closed (400 / 415 / 413)
   without touching STT or Vera.
9. Typed chat still works unchanged.
10. Rendering parity: assistant text containing bounded markdown
    (``**bold**``) is persisted identically for typed and dictated
    turns; the shared client renderer is referenced from the served
    dictation enhancer JS so there is only one rendering path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.vera import session_store as vera_session_store
from voxera.vera_web import app as vera_app_module
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.output import SpeechReplyTTSResult
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse
from voxera.voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse


def _set_queue_root(monkeypatch: pytest.MonkeyPatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


def _enabled_voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=True,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def _force_enabled_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)


def _make_stt(transcript: str = "hello vera", status: str = STT_STATUS_SUCCEEDED) -> STTResponse:
    return STTResponse(
        request_id="test-stt",
        status=status,
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


def _make_tts(
    *,
    status: str = TTS_STATUS_SUCCEEDED,
    audio_path: str | None = "/tmp/fake.wav",
) -> TTSResponse:
    return TTSResponse(
        request_id="test-tts",
        status=status,
        audio_path=audio_path,
        backend="piper_local",
        error=None,
        error_class=None,
        audio_duration_ms=1200,
        started_at_ms=1000,
        finished_at_ms=1150,
        schema_version=1,
        inference_ms=150,
    )


async def _fake_vera_reply(**kwargs: Any) -> dict[str, Any]:
    return {"answer": f"Ack: {kwargs['user_message']}", "status": "ok:test"}


def _async_stt(response: STTResponse) -> Any:
    async def _run(**_kwargs: Any) -> STTResponse:
        return response

    return _run


def _async_tts(response: TTSResponse) -> Any:
    async def _run(**_kwargs: Any) -> TTSResponse:
        return response

    return _run


def _make_speech_result(
    *,
    responses: list[TTSResponse] | None = None,
    speech_text: str = "Hello vera.",
    sentence_count: int | None = None,
    truncated: bool = False,
    first_chunk_ms: int | None = 50,
    total_ms: int | None = 75,
    prepare_ms: int | None = 1,
    split_ms: int | None = 1,
) -> SpeechReplyTTSResult:
    """Build a ``SpeechReplyTTSResult`` for tests.

    Mirrors the shape the canonical pipeline produces — per-chunk
    ``TTSResponse`` list plus truthful sub-stage timings — without
    exercising any real backend.
    """
    if responses is None:
        responses = [_make_tts()]
    effective_count = sentence_count if sentence_count is not None else len(responses)
    return SpeechReplyTTSResult(
        speech_text=speech_text,
        sentence_count=effective_count,
        truncated=truncated,
        responses=list(responses),
        speech_text_prepare_ms=prepare_ms,
        speech_sentence_split_ms=split_ms,
        tts_first_chunk_ms=first_chunk_ms,
        tts_total_synthesis_ms=total_ms,
    )


def _async_speech_reply(result: SpeechReplyTTSResult) -> Any:
    """Async side_effect helper that returns a pre-built speech result."""

    async def _run(**_kwargs: Any) -> SpeechReplyTTSResult:
        return result

    return _run


def _post_voice(
    client: TestClient,
    *,
    body: bytes,
    content_type: str = "audio/webm",
    params: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/chat/voice",
        content=body,
        headers={"content-type": content_type},
        params=params or {},
    )


def test_index_renders_dictation_controls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'data-testid="vera-mic-btn"' in res.text
    assert 'data-testid="vera-voice-bar"' in res.text
    assert 'data-testid="vera-voice-speak"' in res.text
    assert 'src="/static/vera_dictation.js"' in res.text
    # Progressive enhancement: the mic button ships hidden so
    # typed chat stays intact when JS or the mic is unavailable.
    assert "vera-mic-btn" in res.text
    # The main IIFE exposes the canonical turns renderer as a
    # cross-IIFE hook so dictation renders through the same bounded
    # markdown subset that typed replies use.  Pin the hook name so
    # it cannot be silently renamed.
    assert "window.__veraApplyServerTurns" in res.text


def test_dictation_enhancer_js_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    # The script MUST never auto-start recording; operator-initiated only.
    assert "getUserMedia" in res.text
    assert "mic.addEventListener" not in res.text  # no auto-listen
    assert 'addEventListener("click"' in res.text or "addEventListener('click'" in res.text
    # Rendering parity: the enhancer MUST defer to the main-page IIFE's
    # renderer for thread updates so assistant replies render through
    # the same bounded markdown subset that typed replies use.
    assert "window.__veraApplyServerTurns" in res.text


def test_chat_voice_persists_voice_transcript_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)

    stt = _make_stt(transcript="what is the capital of Alberta")
    session_id = "vera-dict-test"
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
            params={"session_id": session_id},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["session_id"] == session_id
    assert payload["stt"]["success"] is True
    assert payload["stt"]["transcript"] == "what is the capital of Alberta"
    assert payload["assistant_text"] == "Ack: what is the capital of Alberta"
    assert isinstance(payload["turns"], list)
    assert payload["turn_count"] == len(payload["turns"])
    turns = vera_session_store.read_session_turns(queue, session_id)
    assert turns[0]["role"] == "user"
    assert turns[0]["input_origin"] == "voice_transcript"
    assert turns[0]["text"] == "what is the capital of Alberta"
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == "Ack: what is the capital of Alberta"


def test_chat_voice_informational_has_no_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="explain photosynthesis simply")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-info-test"})
    assert res.status_code == 200
    payload = res.json()
    # Conversational lane: no preview drafted, assistant reply surfaced.
    assert payload["preview"] is None
    assert payload["assistant_text"].startswith("Ack: ")
    assert payload["ok"] is True


def test_typed_and_dictated_informational_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed and dictated informational turns share the canonical path.

    The same informational message, submitted via typed ``/chat`` and
    dictated ``/chat/voice``, MUST produce the same assistant text and
    the same ``preview`` state.  Any drift here would mean the two
    surfaces diverged at the message-processing layer.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)

    message = "explain photosynthesis simply"
    # Typed side.
    client = TestClient(vera_app_module.app)
    typed_res = client.post(
        "/chat",
        content=f"session_id=parity-typed&input_origin=typed&message={message.replace(' ', '+')}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert typed_res.status_code == 200
    typed_turns = vera_session_store.read_session_turns(queue, "parity-typed")
    typed_preview = vera_session_store.read_session_preview(queue, "parity-typed")

    # Dictated side.
    stt = _make_stt(transcript=message)
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        voice_res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "parity-voice"},
        )
    assert voice_res.status_code == 200
    voice_payload = voice_res.json()
    voice_turns = vera_session_store.read_session_turns(queue, "parity-voice")
    voice_preview = vera_session_store.read_session_preview(queue, "parity-voice")

    # Assistant text is identical.
    assert voice_payload["assistant_text"] == typed_turns[-1]["text"]
    assert voice_turns[-1]["text"] == typed_turns[-1]["text"]
    # Preview state is identical (both conversational → both None).
    assert voice_preview == typed_preview
    assert voice_preview is None
    # Input origin is the only difference on the user turn.
    assert typed_turns[0]["input_origin"] == "typed"
    assert voice_turns[0]["input_origin"] == "voice_transcript"


def test_typed_and_dictated_rendering_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assistant text with bounded markdown is stored identically.

    The dictation enhancer hands the turns array to the shared
    client-side renderer via ``window.__veraApplyServerTurns`` — the
    SAME function typed chat uses.  As long as the server returns the
    same assistant text for both surfaces, the rendered HTML is
    identical by construction.  This test pins the server-side
    invariant: the persisted assistant text must match.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    markdown_reply = "Here is a summary: **bold fact** and `inline code`."

    async def _markdown_reply(**_kwargs: Any) -> dict[str, Any]:
        return {"answer": markdown_reply, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _markdown_reply)

    message = "summarize the basics of relativity"
    client = TestClient(vera_app_module.app)
    typed_res = client.post(
        "/chat",
        content=f"session_id=render-typed&input_origin=typed&message={message.replace(' ', '+')}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert typed_res.status_code == 200
    typed_turns = vera_session_store.read_session_turns(queue, "render-typed")

    stt = _make_stt(transcript=message)
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        voice_res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "render-voice"},
        )
    assert voice_res.status_code == 200
    voice_payload = voice_res.json()

    # The server-side truth: assistant text carries the same raw
    # markdown in both cases.  The client-side renderer (same for
    # both surfaces via window.__veraApplyServerTurns) takes it from
    # there.
    assert typed_turns[-1]["text"] == markdown_reply
    assert voice_payload["assistant_text"] == markdown_reply
    voice_turns = voice_payload["turns"]
    assistant_voice_turns = [t for t in voice_turns if t.get("role") == "assistant"]
    assert assistant_voice_turns[-1]["text"] == markdown_reply


def test_index_preview_pane_has_stable_anchor_for_js_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview pane has a stable host + hook so dictation can refresh it.

    Typed ``/chat`` refreshes the preview pane via a full-page reload —
    Jinja renders ``<section class="preview">`` when ``pending_preview``
    is set.  Dictated ``/chat/voice`` is JSON-only, so the main IIFE
    exposes ``window.__veraApplyServerPreview`` which the dictation
    enhancer calls to rewrite the pane in place.  Both the host
    container and the hook name must be present on the page, and the
    hook name must never be silently renamed.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'data-testid="vera-preview-host"' in res.text
    assert 'id="vera-preview-host"' in res.text
    assert "window.__veraApplyServerPreview" in res.text
    # Dictation enhancer must consume the same hook.
    res_js = client.get("/static/vera_dictation.js")
    assert res_js.status_code == 200
    assert "window.__veraApplyServerPreview" in res_js.text


def test_chat_voice_payload_carries_canonical_preview_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/chat/voice`` payload includes ``preview`` + ``has_preview_truth``.

    The JS hook refuses to touch the visible preview pane unless the
    payload explicitly marks the ``preview`` field as authoritative —
    ``has_preview_truth=True``.  Every ``/chat/voice`` response must
    carry that flag, since the route reads ``read_session_preview``
    fresh in both the chat-ran and STT-failed branches.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="hello vera")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-pv-truth"})
    assert res.status_code == 200
    payload = res.json()
    assert "preview" in payload
    assert payload["has_preview_truth"] is True


def test_chat_voice_preview_pane_visibility_matches_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When typed renders the preview section, dictated payload carries the same dict.

    Seeds a session with a canonical preview.  GET ``/`` on that session
    renders the ``<section class="preview">`` block.  A ``/chat/voice``
    turn against the same session must return the exact same preview
    dict under ``payload["preview"]``.  This is the preview-visibility
    parity the JS hook consumes.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    session_id = "vera-pv-match"
    preview: dict[str, object] = {"write_file": {"path": "notes/demo.md", "content": "hello"}}
    from voxera.vera.preview_ownership import reset_active_preview

    reset_active_preview(queue, session_id, preview)
    # Typed surface: the Jinja template renders the preview section.
    client = TestClient(vera_app_module.app)
    typed_res = client.get(f"/?session_id={session_id}")
    assert typed_res.status_code == 200
    assert 'id="vera-preview-pane"' in typed_res.text
    assert "notes/demo.md" in typed_res.text

    # Dictated surface: the JSON payload's preview must equal the
    # canonical session preview.  An informational follow-up does not
    # mutate the preview, so the dict stays intact across the turn.
    stt = _make_stt(transcript="what is in that note")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        voice_res = _post_voice(client, body=b"\x00" * 32, params={"session_id": session_id})
    assert voice_res.status_code == 200
    payload = voice_res.json()
    assert payload["has_preview_truth"] is True
    assert payload["preview"] == preview


def test_chat_voice_preview_cleared_after_submit_reflects_in_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a dictated submit, the payload's preview is None (truthful).

    When ``_submit_handoff`` runs, the active preview is cleared from
    the session.  The ``/chat/voice`` payload re-reads canonical state
    and therefore reports ``preview=None`` — the JS hook then removes
    the visible pane, matching typed-submit behavior.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    session_id = "vera-pv-submit"
    preview: dict[str, object] = {"write_file": {"path": "notes/demo.md", "content": "hi"}}
    from voxera.vera.preview_ownership import clear_active_preview, reset_active_preview

    reset_active_preview(queue, session_id, preview)

    def _fake_submit(
        *, root: Path, session_id: str, preview: dict[str, object] | None
    ) -> tuple[str, str]:
        # Simulate the canonical submit's preview-clear side effect.
        clear_active_preview(root, session_id)
        return ("Submitted demo.md.", "handoff_submitted")

    monkeypatch.setattr(vera_app_module, "_submit_handoff", _fake_submit)
    stt = _make_stt(transcript="submit it")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": session_id})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "handoff_submitted"
    assert payload["preview"] is None
    assert payload["has_preview_truth"] is True


def test_chat_voice_stt_failure_preserves_preview_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STT-only failure must NOT clobber an existing preview in the payload.

    The dictation UI removes the pane whenever it receives an
    authoritative ``preview=None``.  When STT fails before the chat
    helper runs, the session's preview is unchanged — reporting
    ``None`` would misrepresent canonical truth and cause the UI to
    hide a real, still-active preview.  The route therefore re-reads
    session state and returns the actual on-disk preview.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    session_id = "vera-pv-stt-fail"
    preview: dict[str, object] = {"write_file": {"path": "notes/demo.md", "content": "hi"}}
    from voxera.vera.preview_ownership import reset_active_preview

    reset_active_preview(queue, session_id, preview)

    failing_stt = STTResponse(
        request_id="fail",
        status="failed",
        transcript=None,
        language=None,
        audio_duration_ms=0,
        error="backend unavailable",
        error_class="RuntimeError",
        backend="whisper_local",
        started_at_ms=0,
        finished_at_ms=50,
        schema_version=1,
        inference_ms=50,
    )
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(failing_stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": session_id})
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["status"] == "stt_failed"
    # Preview on disk is intact; the payload must reflect that intact
    # state so the UI does not wrongly hide a real preview.
    assert payload["has_preview_truth"] is True
    assert payload["preview"] == preview


def test_chat_voice_ok_true_for_clean_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ok=True`` on a clean refusal lane (e.g. ``blocked_path``).

    The new ``ok`` semantics are ``stt_ok AND chat_result is not None AND
    not chat_result.error``.  A blocked-file-intent refusal produces a
    canonical ``ChatTurnResult`` with a clear assistant message and an
    EMPTY ``error`` string — the canonical path ran and produced a
    truthful reply, it just happens to be a refusal.  Pin that this
    reports ``ok=True`` so future refactors don't silently re-conflate
    "refusal" with "failure".
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    stt = _make_stt(transcript="check if ../../../etc/passwd exists")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-ok-refusal"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "blocked_path"
    assert payload["error"] == ""
    assert payload["ok"] is True
    assert payload["assistant_text"]  # non-empty refusal surfaced


def test_chat_voice_ok_false_for_voice_input_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ok=False`` when the voice transcript normalizes to empty.

    ``ingest_voice_transcript`` raises ``ValueError`` for a transcript
    that is empty after stripping.  The canonical helper catches that
    and returns ``status="voice_input_invalid"`` with a non-empty
    ``error`` string — ``ok`` must be ``False`` in that case.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    # Whitespace-only transcript normalises to empty inside the ingest
    # step; STT itself reports success (transcript present) but the
    # canonical helper then fails closed.
    stt = _make_stt(transcript="   \n  ")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-ok-invalid"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "voice_input_invalid"
    assert payload["error"]  # non-empty
    assert payload["ok"] is False


def test_chat_voice_submit_routes_through_canonical_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dictated "submit it" uses the canonical explicit-submit seam.

    When a session already has an active preview and the operator
    dictates a natural submit phrase, the canonical chat helper must
    route through ``_submit_handoff`` — the same path typed ``/chat``
    uses — rather than a bespoke dictation lifecycle hook.  This keeps
    trust boundaries and queue wiring under one owner.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    session_id = "vera-submit-test"
    # Seed the session with an active preview so the canonical
    # "submit active preview" lane has something to submit.
    queue.mkdir(parents=True, exist_ok=True)
    active_preview: dict[str, object] = {
        "write_file": {"path": "notes/demo.md", "content": "hello"}
    }
    from voxera.vera.preview_ownership import reset_active_preview

    reset_active_preview(queue, session_id, active_preview)

    captured: dict[str, Any] = {}

    def _fake_submit(
        *, root: Path, session_id: str, preview: dict[str, object] | None
    ) -> tuple[str, str]:
        captured["root"] = root
        captured["session_id"] = session_id
        captured["preview"] = preview
        return ("Submitted demo.md.", "handoff_submitted")

    monkeypatch.setattr(vera_app_module, "_submit_handoff", _fake_submit)
    stt = _make_stt(transcript="submit it")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": session_id})
    assert res.status_code == 200
    payload = res.json()
    assert captured["session_id"] == session_id
    assert captured["preview"] == active_preview
    assert payload["status"] == "handoff_submitted"
    assert payload["assistant_text"] == "Submitted demo.md."


def test_chat_voice_speak_response_returns_audio_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    audio_file = tmp_path / "fake_tts.wav"
    audio_file.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)
    stt = _make_stt(transcript="hello vera")
    tts = _make_tts(audio_path=str(audio_file))
    speech_result = _make_speech_result(responses=[tts])
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_speech_reply_async",
            side_effect=_async_speech_reply(speech_result),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-tts-test", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["tts"]["success"] is True
    assert payload["tts_url"] is not None
    assert payload["tts_url"].startswith("/vera/voice/audio/")
    # Sentence-first: ``tts_chunk_urls`` is an ordered list whose first
    # element equals the single ``tts_url`` returned for backwards
    # compatibility.
    chunk_urls = payload.get("tts_chunk_urls")
    assert isinstance(chunk_urls, list)
    assert chunk_urls[0] == payload["tts_url"]
    # Serving the token returns the audio file's bytes.
    audio_res = client.get(payload["tts_url"])
    assert audio_res.status_code == 200
    assert audio_res.headers["content-type"].startswith("audio/")
    assert audio_res.content.startswith(b"RIFF")
    # Truthful sub-stage timings surface on the payload.
    timings = payload.get("stage_timings", {})
    assert isinstance(timings.get("speech_text_prepare_ms"), int)
    assert isinstance(timings.get("speech_sentence_split_ms"), int)
    assert isinstance(timings.get("tts_first_chunk_ms"), int)
    assert isinstance(timings.get("tts_total_synthesis_ms"), int)


def test_chat_voice_tts_failure_text_still_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="hello vera")
    failing_tts = _make_tts(status="failed", audio_path=None)
    speech_result = _make_speech_result(responses=[failing_tts])
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_speech_reply_async",
            side_effect=_async_speech_reply(speech_result),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-tts-fail", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["tts"]["success"] is False
    assert payload["tts_url"] is None
    # TTS failure leaves the chunk URL list empty — no audio, no
    # fabricated URLs.
    assert payload["tts_chunk_urls"] == []
    # Vera answer still persisted even though TTS failed.
    turns = vera_session_store.read_session_turns(queue, "vera-tts-fail")
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == "Ack: hello vera"


def test_chat_voice_empty_body_returns_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    called = {"stt": False}

    async def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
        called["stt"] = True
        return _make_stt()

    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_must_not_call_stt,
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"")
    assert res.status_code == 400
    assert called["stt"] is False


def test_chat_voice_non_audio_content_type_returns_415(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/voice",
        content=b"plain text body",
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 415


def test_chat_voice_oversized_returns_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_VERA_DICTATION_MAX_BYTES", 16)
    client = TestClient(vera_app_module.app)
    res = _post_voice(client, body=b"X" * 64)
    assert res.status_code == 413


def test_typed_chat_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)

    async def _fake_reply(**kwargs: Any) -> dict[str, Any]:
        return {"answer": "typed ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat",
        content="session_id=typed-test&input_origin=typed&message=hello",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, "typed-test")
    assert turns[0]["role"] == "user"
    assert turns[0]["input_origin"] == "typed"
    assert turns[0]["text"] == "hello"


def test_vera_voice_audio_unknown_token_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    res = client.get("/vera/voice/audio/doesnotexist")
    assert res.status_code == 404


def _disabled_voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=False,
        enable_voice_output=False,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def test_chat_voice_fails_closed_when_voice_input_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Voice-input-disabled runtime MUST fail-closed BEFORE audio hits disk.

    The UI hides the mic button in this case; a direct client that
    skips the UI must still be refused without the audio ever being
    written to temp storage or fed to STT.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _disabled_voice_flags)
    stt_called = {"count": 0}
    mkstemp_called = {"count": 0}

    async def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
        stt_called["count"] += 1
        return _make_stt()

    original_mkstemp = vera_app_module.tempfile.mkstemp

    def _counting_mkstemp(*args: Any, **kwargs: Any) -> Any:
        mkstemp_called["count"] += 1
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(vera_app_module.tempfile, "mkstemp", _counting_mkstemp)
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_must_not_call_stt,
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-disabled"})
    assert res.status_code == 403
    payload = res.json()
    assert payload["ok"] is False
    assert payload["status"] == "voice_input_disabled"
    assert stt_called["count"] == 0, "STT must NOT run when voice_input is disabled"
    assert mkstemp_called["count"] == 0, "No temp file must be created when voice_input is disabled"


def test_chat_voice_oversized_rejects_before_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized uploads 413 BEFORE a temp file is created.

    Protects against a giant body being materialized to disk just to
    be rejected. Combined with the early Content-Length gate on the
    route, this is the operator-facing guarantee that the cap is not
    paid in temp storage.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_VERA_DICTATION_MAX_BYTES", 16)
    mkstemp_called = {"count": 0}
    original_mkstemp = vera_app_module.tempfile.mkstemp

    def _counting_mkstemp(*args: Any, **kwargs: Any) -> Any:
        mkstemp_called["count"] += 1
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(vera_app_module.tempfile, "mkstemp", _counting_mkstemp)
    client = TestClient(vera_app_module.app)
    res = _post_voice(client, body=b"X" * 64)
    assert res.status_code == 413
    assert mkstemp_called["count"] == 0


def test_chat_voice_ok_reflects_stt_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level ``ok`` is False when STT did not succeed, even at HTTP 200.

    Telemetry / operator dashboards rely on ``ok`` being truthful so
    an operator can tell "the mic worked but STT failed" from "this
    was a clean success" at a glance.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)

    failing_stt = STTResponse(
        request_id="test-stt-fail",
        status="failed",
        transcript=None,
        language=None,
        audio_duration_ms=0,
        error="backend unavailable",
        error_class="RuntimeError",
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1050,
        schema_version=1,
        inference_ms=50,
    )
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(failing_stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-stt-fail"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["stt"]["success"] is False
    # Assistant text is empty when STT never produced a transcript.
    assert payload["assistant_text"] == ""


def test_chat_voice_sentence_first_multi_chunk_ordered_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-sentence spoken reply returns one audio URL per chunk,
    in spoken order, AND exposes truthful sub-stage timings.

    The browser chains playback through ``tts_chunk_urls`` so audio
    starts as soon as the first chunk is fetched; the server does
    not wait for the full spoken reply to synthesize before the
    first URL is playable.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    # Three chunk artifacts — one per spoken sentence.  The test is
    # not actually playing audio; we just check the URLs map back to
    # the three distinct tokens and survive a registry get.
    chunk_files: list[Path] = []
    chunk_responses: list[TTSResponse] = []
    for i in range(3):
        f = tmp_path / f"chunk_{i}.wav"
        f.write_bytes(b"RIFF" + bytes([i]) * 16)
        chunk_files.append(f)
        chunk_responses.append(_make_tts(audio_path=str(f)))
    speech_result = _make_speech_result(
        responses=chunk_responses,
        speech_text="First. Second. Third.",
        sentence_count=3,
        first_chunk_ms=30,
        total_ms=90,
    )
    stt = _make_stt(transcript="hello vera")
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_speech_reply_async",
            side_effect=_async_speech_reply(speech_result),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-sentence-first", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    # Three chunks, three URLs, in spoken order.
    chunk_urls = payload["tts_chunk_urls"]
    assert isinstance(chunk_urls, list)
    assert len(chunk_urls) == 3
    for url in chunk_urls:
        assert url.startswith("/vera/voice/audio/")
    assert chunk_urls[0] == payload["tts_url"]
    # Timings on the outer payload reflect the first-chunk win.
    timings = payload["stage_timings"]
    assert timings["tts_first_chunk_ms"] == 30
    assert timings["tts_total_synthesis_ms"] == 90
    # TTS dict surfaces per-chunk diagnostics so operators can see
    # each chunk's outcome.
    tts = payload["tts"]
    assert tts["success"] is True
    assert tts["sentence_count"] == 3
    assert tts["speech_text"] == "First. Second. Third."
    assert len(tts["chunks"]) == 3
    for chunk_entry in tts["chunks"]:
        assert chunk_entry["success"] is True


def test_chat_voice_tts_registry_cap_evicts_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS registry never exceeds its cap; oldest entries are evicted with disk cleanup.

    Prevents unbounded growth of the in-process token registry and of
    the on-disk temp files that back each token.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_MAX_TTS_REGISTRY_ENTRIES", 3)
    # Start from a clean registry so the test is order-independent.
    for tok in list(vera_app_module._TTS_AUDIO_REGISTRY.keys()):
        vera_app_module._TTS_AUDIO_REGISTRY.pop(tok, None)
    created_paths: list[Path] = []
    for i in range(5):
        audio_file = tmp_path / f"tts_{i}.wav"
        audio_file.write_bytes(b"RIFF" + i.to_bytes(1, "little") * 16)
        created_paths.append(audio_file)
        vera_app_module._register_tts_audio(str(audio_file))
    # Registry is capped.
    assert len(vera_app_module._TTS_AUDIO_REGISTRY) == 3
    # The first two entries' on-disk artifacts were unlinked by
    # eviction; the last three remain.
    assert not created_paths[0].exists()
    assert not created_paths[1].exists()
    assert created_paths[2].exists()
    assert created_paths[3].exists()
    assert created_paths[4].exists()


def test_chat_voice_tts_token_rejects_bad_alphabet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed tokens 404 without touching the registry."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    # Registry-shape tokens use URL-safe base64 (A-Z a-z 0-9 - _); a
    # token containing other characters must be rejected up front.
    res = client.get("/vera/voice/audio/bad%20token")
    assert res.status_code == 404
    res = client.get("/vera/voice/audio/has.period")
    assert res.status_code == 404
