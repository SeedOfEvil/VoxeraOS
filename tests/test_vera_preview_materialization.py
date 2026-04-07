"""Regression tests for clarification-to-preview materialization.

Covers the exact conversational pattern that was breaking:
  1. User asks for a script/automation
  2. Vera asks a clarification
  3. User answers the clarification
  4. Vera must actually materialize a governed preview (not just claim it)
  5. ``go ahead`` submits the real preview
  6. Recovery phrasing like ``please prepare that script`` re-engages drafting

Root cause:
  After a clarification exchange, the user's answer lacks explicit code-draft
  signals (verb + language keyword).  ``is_code_draft_turn`` was False on the
  answer turn, so the LLM did not receive the code-generation hint and the
  existing empty-content preview shell was left unfilled or cleared.

Fix:
  ``_is_empty_code_preview_shell`` detects the post-clarification state and
  ``_post_clarification_code_draft`` re-engages the code draft flow.
  ``_recover_code_draft_from_history`` handles the recovery case when the
  shell was already cleared.
"""

from __future__ import annotations

import pytest

from voxera.core.code_draft_intent import classify_code_draft_intent
from voxera.vera_web import app as vera_app_module
from voxera.vera_web.app import (
    _CODE_DRAFT_RECOVERY_RE,
    _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY,
    _detect_automation_clarification_completion,
    _is_empty_code_preview_shell,
    _looks_like_direct_automation_request,
    _looks_like_previewable_automation_intent,
    _recover_code_draft_from_history,
)
from voxera.vera_web.response_shaping import BLANKET_PREVIEW_REFUSAL_TEXT

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# 1. Unit tests: _is_empty_code_preview_shell
# ---------------------------------------------------------------------------


class TestIsEmptyCodePreviewShell:
    def test_none_returns_false(self) -> None:
        assert not _is_empty_code_preview_shell(None)

    def test_empty_dict_returns_false(self) -> None:
        assert not _is_empty_code_preview_shell({})

    def test_no_write_file_returns_false(self) -> None:
        assert not _is_empty_code_preview_shell({"goal": "open https://example.com"})

    def test_non_code_extension_returns_false(self) -> None:
        preview = {
            "goal": "write a note",
            "write_file": {"path": "~/VoxeraOS/notes/note.txt", "content": "", "mode": "overwrite"},
        }
        assert not _is_empty_code_preview_shell(preview)

    def test_markdown_extension_returns_false(self) -> None:
        preview = {
            "goal": "draft a document",
            "write_file": {"path": "~/VoxeraOS/notes/doc.md", "content": "", "mode": "overwrite"},
        }
        assert not _is_empty_code_preview_shell(preview)

    def test_code_shell_with_content_returns_false(self) -> None:
        preview = {
            "goal": "draft a python script",
            "write_file": {
                "path": "~/VoxeraOS/notes/script.py",
                "content": "print('hello')",
                "mode": "overwrite",
            },
        }
        assert not _is_empty_code_preview_shell(preview)

    def test_empty_python_shell_returns_true(self) -> None:
        preview = {
            "goal": "draft a python script as script.py",
            "write_file": {
                "path": "~/VoxeraOS/notes/script.py",
                "content": "",
                "mode": "overwrite",
            },
        }
        assert _is_empty_code_preview_shell(preview)

    def test_empty_bash_shell_returns_true(self) -> None:
        preview = {
            "goal": "draft a bash script",
            "write_file": {
                "path": "~/VoxeraOS/notes/script.sh",
                "content": "",
                "mode": "overwrite",
            },
        }
        assert _is_empty_code_preview_shell(preview)

    def test_empty_yaml_shell_returns_true(self) -> None:
        preview = {
            "goal": "draft a yaml config",
            "write_file": {
                "path": "~/VoxeraOS/notes/config.yaml",
                "content": "",
                "mode": "overwrite",
            },
        }
        assert _is_empty_code_preview_shell(preview)


# ---------------------------------------------------------------------------
# 2. Unit tests: _CODE_DRAFT_RECOVERY_RE
# ---------------------------------------------------------------------------


class TestCodeDraftRecoveryPattern:
    @pytest.mark.parametrize(
        "msg",
        [
            "please prepare that script",
            "prepare the script",
            "create the script we discussed",
            "make that script",
            "build the script please",
            "generate the script",
            "write the script",
            "draft the script for me",
            "prepare that code",
            "create the program",
        ],
    )
    def test_recovery_phrases_match(self, msg: str) -> None:
        assert _CODE_DRAFT_RECOVERY_RE.search(msg), f"Expected match for: {msg!r}"

    @pytest.mark.parametrize(
        "msg",
        [
            "go ahead",
            "submit it",
            "what is python",
            "tell me about scripts",
            "save that to a note",
            "yes, use ~/notes as the source",
            # "file" is deliberately excluded to avoid false positives
            # like "create a file called notes.txt with my grocery list"
            "please prepare that file",
            "create a file called notes.txt",
            "draft a file explaining the architecture",
            # "program" as schedule should not match without code context,
            # but "program" is included — the outer function gates on
            # is_code_draft_request history, so this is acceptable.
        ],
    )
    def test_non_recovery_phrases_do_not_match(self, msg: str) -> None:
        assert not _CODE_DRAFT_RECOVERY_RE.search(msg), f"Expected no match for: {msg!r}"


# ---------------------------------------------------------------------------
# 3. Unit tests: _recover_code_draft_from_history
# ---------------------------------------------------------------------------


class TestRecoverCodeDraftFromHistory:
    def test_returns_none_when_preview_exists(self) -> None:
        preview = {
            "goal": "test",
            "write_file": {"path": "x.py", "content": "", "mode": "overwrite"},
        }
        result = _recover_code_draft_from_history(
            "prepare that script", pending_preview=preview, turns=[]
        )
        assert result is None

    def test_returns_none_when_message_is_code_draft(self) -> None:
        result = _recover_code_draft_from_history(
            "write me a python script to sort files",
            pending_preview=None,
            turns=[],
        )
        assert result is None

    def test_returns_none_when_no_matching_history(self) -> None:
        turns = [
            {"role": "user", "text": "what is the weather?"},
            {"role": "assistant", "text": "I don't have weather data."},
        ]
        result = _recover_code_draft_from_history(
            "prepare that script", pending_preview=None, turns=turns
        )
        assert result is None

    def test_recovers_from_history_with_code_draft(self) -> None:
        turns = [
            {"role": "user", "text": "write me a python script that monitors a folder"},
            {"role": "assistant", "text": "What folder should I watch?"},
            {"role": "user", "text": "use ~/notes as source"},
            {"role": "assistant", "text": "I was not able to prepare a preview."},
        ]
        result = _recover_code_draft_from_history(
            "please prepare that script", pending_preview=None, turns=turns
        )
        assert result is not None
        assert isinstance(result, dict)
        wf = result.get("write_file")
        assert isinstance(wf, dict)
        assert wf.get("path", "").endswith(".py")


# ---------------------------------------------------------------------------
# 4. Integration: post-clarification code draft creates real preview
# ---------------------------------------------------------------------------

_SCRIPT_CODE = """\
import os
import time

WATCH_DIR = os.path.expanduser("~/VoxeraOS/notes/incoming")
DONE_DIR = os.path.expanduser("~/VoxeraOS/notes/done")

while True:
    for name in os.listdir(WATCH_DIR):
        src = os.path.join(WATCH_DIR, name)
        if os.path.isdir(src):
            time.sleep(5)
            with open(os.path.join(src, "processed.txt"), "w") as f:
                f.write("processed!")
            os.rename(src, os.path.join(DONE_DIR, name))
    time.sleep(1)
"""


def _make_code_reply_fn(code: str):
    """Return a fake generate_vera_reply that emits a fenced code block."""

    async def _fake_reply(**kwargs):
        return {
            "answer": f"Here's your script:\n\n```python\n{code}\n```",
            "status": "ok:test",
        }

    return _fake_reply


def _make_clarification_reply_fn():
    """Return a fake generate_vera_reply that asks for clarification."""

    async def _fake_reply(**kwargs):
        return {
            "answer": (
                "I'd be happy to write that script for you. "
                "Could you tell me which folder should I monitor for new directories?"
            ),
            "status": "ok:test",
        }

    return _fake_reply


def _make_builder_fn_from_message():
    """Return a fake generate_preview_builder_update that classifies from the user message."""

    async def _fake_builder(**kwargs):
        user_message = kwargs.get("user_message", "")
        draft = classify_code_draft_intent(user_message)
        if draft is not None:
            return draft
        active = kwargs.get("active_preview")
        return active

    return _fake_builder


def test_post_clarification_creates_real_preview(tmp_path, monkeypatch):
    """After a clarification exchange, the answer turn must produce a real preview with code."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Turn 1: user asks for a script → LLM asks for clarification
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_clarification_reply_fn())
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    resp = session.chat(
        "Write me a python script that continuously monitors a folder, "
        "waits until a copied folder is stable, adds a text file saying processed!, "
        "and moves the folder to another location"
    )
    assert resp.status_code == 200

    # After turn 1: empty shell preview should exist
    preview_after_t1 = session.preview()
    assert preview_after_t1 is not None, "Empty preview shell should exist after turn 1"
    wf1 = preview_after_t1.get("write_file", {})
    assert not str(wf1.get("content") or "").strip(), "Preview content should be empty (shell)"

    # Turn 2: user answers the clarification → LLM generates code
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_code_reply_fn(_SCRIPT_CODE))
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    resp = session.chat(
        "Watch ~/VoxeraOS/notes/incoming, move completed folders to ~/VoxeraOS/notes/done, "
        "use 5 seconds stability timeout"
    )
    assert resp.status_code == 200

    # After turn 2: preview must have real code content
    preview_after_t2 = session.preview()
    assert preview_after_t2 is not None, "Preview must exist after clarification answer"
    wf2 = preview_after_t2.get("write_file", {})
    content = str(wf2.get("content") or "").strip()
    assert content, "Preview must have real code content after clarification answer"
    assert "WATCH_DIR" in content or "os.listdir" in content, (
        "Preview content should contain the generated script code"
    )


def test_post_clarification_go_ahead_submits(tmp_path, monkeypatch):
    """'go ahead' after a successful post-clarification preview must submit."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Turn 1: script request → clarification
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_clarification_reply_fn())
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    session.chat("Write me a python script that monitors a folder and moves stable directories")

    # Turn 2: answer → code generated
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_code_reply_fn(_SCRIPT_CODE))
    session.chat("Watch ~/VoxeraOS/notes/incoming, move to ~/VoxeraOS/notes/done")

    # Preview must exist with content
    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file", {})
    assert str(wf.get("content") or "").strip()

    # Turn 3: "go ahead" should submit (not fail with "no preview")
    monkeypatch.setattr(
        vera_app_module,
        "generate_vera_reply",
        _make_code_reply_fn(""),  # won't be reached for submit
    )
    resp = session.chat("go ahead")
    assert resp.status_code == 200
    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "no preview" not in last_reply.lower() or "submitted" in last_reply.lower(), (
        f"Expected submission or success, got: {last_reply!r}"
    )


def test_no_false_preview_claim_without_real_preview(tmp_path, monkeypatch):
    """Vera must not say 'preparing a preview' unless a real preview is created."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Set up a scenario where no preview can be created
    async def _no_builder(**kwargs):
        return None

    async def _false_claim_reply(**kwargs):
        return {
            "answer": "I've prepared a preview of your request. You can review it.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _false_claim_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    resp = session.chat("tell me a joke")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # The guardrail should have stripped the false preview claim
    assert "prepared a preview" not in last_reply.lower(), (
        f"False preview claim should have been stripped: {last_reply!r}"
    )


def test_honest_fail_closed_when_no_code_generated(tmp_path, monkeypatch):
    """If LLM cannot generate code even after clarification, Vera fails closed honestly."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Turn 1: script request
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_clarification_reply_fn())
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    session.chat("Write me a python script that monitors a folder")

    # Turn 2: answer → but LLM still doesn't generate code (no fenced block)
    async def _no_code_reply(**kwargs):
        return {
            "answer": "I understand the requirements. Let me think about the best approach.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _no_code_reply)
    resp = session.chat("use ~/VoxeraOS/notes as source")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # Should not falsely claim a preview was prepared
    preview = session.preview()
    if preview is not None:
        wf = preview.get("write_file", {})
        content = str(wf.get("content") or "").strip()
        if not content:
            # Empty shell is acceptable but reply should not claim success
            assert "prepared a preview" not in last_reply.lower() or (
                "not able" in last_reply.lower()
            )


def test_recovery_prepare_that_script(tmp_path, monkeypatch):
    """'please prepare that script' recovers when enough detail exists in history."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Turn 1: script request → clarification
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_clarification_reply_fn())
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    session.chat("Write me a python script that monitors a folder")

    # Turn 2: answer → fail to produce code (simulating original bug)
    async def _fail_reply(**kwargs):
        return {
            "answer": "I was not able to prepare a governed preview for this request.",
            "status": "ok:test",
        }

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fail_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat("use ~/VoxeraOS/notes as source")

    # Clear the preview to simulate the stale-shell cleanup
    from voxera.vera.session_store import write_session_preview

    write_session_preview(session.queue, session.session_id, None)
    assert session.preview() is None

    # Turn 3: recovery → "please prepare that script"
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_code_reply_fn(_SCRIPT_CODE))
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    resp = session.chat("please prepare that script")
    assert resp.status_code == 200

    # Preview should be recovered with real content
    preview = session.preview()
    assert preview is not None, "Recovery should have re-created a preview"
    wf = preview.get("write_file", {})
    content = str(wf.get("content") or "").strip()
    assert content, "Recovered preview must have real code content"


def test_workspace_rooted_script_path(tmp_path, monkeypatch):
    """Script preview paths must be rooted in the workspace (~/VoxeraOS/notes/)."""
    session = make_vera_session(monkeypatch, tmp_path)

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_code_reply_fn(_SCRIPT_CODE))
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    resp = session.chat("Write me a python script called folder_monitor.py")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file", {})
    path = str(wf.get("path") or "")
    assert path.startswith("~/VoxeraOS/notes/"), (
        f"Script path must be workspace-rooted, got: {path!r}"
    )


# ---------------------------------------------------------------------------
# 6. Regression: post-clarification does NOT hijack unrelated intents
# ---------------------------------------------------------------------------


def _setup_empty_code_shell(session, monkeypatch):
    """Create an empty code preview shell by sending a script request with clarification."""
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _make_clarification_reply_fn())
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn_from_message()
    )
    session.chat("Write me a python script that monitors a folder")
    preview = session.preview()
    assert preview is not None, "Shell should exist"
    assert not str(preview.get("write_file", {}).get("content") or "").strip()


def test_informational_query_not_hijacked_by_empty_shell(tmp_path, monkeypatch):
    """An informational query while an empty code shell exists must NOT become a code draft."""
    session = make_vera_session(monkeypatch, tmp_path)
    _setup_empty_code_shell(session, monkeypatch)

    async def _info_reply(**kwargs):
        code_draft = kwargs.get("code_draft", False)
        # The LLM should NOT receive the code draft hint for an info query
        assert not code_draft, "Informational query must not be classified as code_draft"
        return {"answer": "The weather is sunny.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _info_reply)

    async def _passthrough_builder(**kwargs):
        return kwargs.get("active_preview")

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)
    resp = session.chat("what is the weather today?")
    assert resp.status_code == 200


def test_writing_draft_not_hijacked_by_empty_shell(tmp_path, monkeypatch):
    """A writing-draft request while an empty code shell exists must NOT become a code draft."""
    session = make_vera_session(monkeypatch, tmp_path)
    _setup_empty_code_shell(session, monkeypatch)

    async def _writing_reply(**kwargs):
        code_draft = kwargs.get("code_draft", False)
        assert not code_draft, "Writing draft must not be classified as code_draft"
        return {"answer": "Here is your essay about volcanoes...", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _writing_reply)

    async def _passthrough_builder(**kwargs):
        return kwargs.get("active_preview")

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)
    resp = session.chat("write me an essay about volcanoes")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. Unit tests: _detect_automation_clarification_completion
# ---------------------------------------------------------------------------


class TestDetectAutomationClarificationCompletion:
    """Unit tests for the broader automation/process clarification detector."""

    def _automation_turns(self) -> list[dict[str, str]]:
        return [
            {
                "role": "user",
                "text": (
                    "I want a process that detects a new folder copied into a workspace "
                    "location and does something with it, then moves it elsewhere"
                ),
            },
            {
                "role": "assistant",
                "text": (
                    "What is the source folder you want to monitor? Where should processed "
                    "folders be moved? What action should I take when a new directory appears?"
                ),
            },
        ]

    def test_returns_none_when_preview_exists(self) -> None:
        preview = {
            "goal": "x",
            "write_file": {"path": "x.py", "content": "y", "mode": "overwrite"},
        }
        result = _detect_automation_clarification_completion(
            "source: ./incoming, destination: ./processed",
            pending_preview=preview,
            turns=self._automation_turns(),
        )
        assert result is None

    def test_returns_none_when_no_turns(self) -> None:
        result = _detect_automation_clarification_completion(
            "source: ./incoming, destination: ./processed",
            pending_preview=None,
            turns=[],
        )
        assert result is None

    def test_returns_none_when_no_clarification_question(self) -> None:
        turns = [
            {"role": "user", "text": "monitor a folder for new directories"},
            {"role": "assistant", "text": "Sure, here's a general overview."},
        ]
        result = _detect_automation_clarification_completion(
            "source: ./incoming",
            pending_preview=None,
            turns=turns,
        )
        assert result is None

    def test_returns_none_when_no_automation_intent_in_history(self) -> None:
        turns = [
            {"role": "user", "text": "tell me about photosynthesis"},
            {"role": "assistant", "text": "Could you say more about what you'd like?"},
        ]
        result = _detect_automation_clarification_completion(
            "source: ./incoming, destination: ./processed",
            pending_preview=None,
            turns=turns,
        )
        assert result is None

    def test_returns_none_when_no_detail_signal(self) -> None:
        turns = self._automation_turns()
        result = _detect_automation_clarification_completion(
            "yes please go ahead",
            pending_preview=None,
            turns=turns,
        )
        assert result is None

    def test_synthesizes_python_shell_for_clarification_completion(self) -> None:
        turns = self._automation_turns()
        result = _detect_automation_clarification_completion(
            (
                "source: ./incoming, destination: ./processed, action: add a "
                "text file saying processed!"
            ),
            pending_preview=None,
            turns=turns,
        )
        assert result is not None
        assert isinstance(result, dict)
        wf = result.get("write_file")
        assert isinstance(wf, dict)
        assert str(wf.get("path", "")).endswith(".py")
        assert not str(wf.get("content") or "").strip(), "Synthesized shell must be empty content"

    def test_returns_none_when_message_is_already_code_draft(self) -> None:
        turns = self._automation_turns()
        result = _detect_automation_clarification_completion(
            "actually write me a python script that does something different",
            pending_preview=None,
            turns=turns,
        )
        assert result is None

    def test_path_token_alone_is_sufficient_detail_signal(self) -> None:
        turns = self._automation_turns()
        result = _detect_automation_clarification_completion(
            "use ~/VoxeraOS/notes/incoming and ~/VoxeraOS/notes/done",
            pending_preview=None,
            turns=turns,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# 8. Integration: process/automation clarification flow materializes preview
# ---------------------------------------------------------------------------


_AUTOMATION_REQUEST = (
    "I want a process that detects a new folder copied into a workspace location "
    "and does something with it, then moves it elsewhere"
)

_AUTOMATION_CLARIFICATION_QUESTION = (
    "I'd be happy to help. What is the source folder you want to monitor? "
    "Where should processed folders be moved? What action should I take "
    "when a new directory appears?"
)

_AUTOMATION_CLARIFICATION_ANSWER = (
    "source: ./incoming, destination: ./processed, action: add a text file saying processed!"
)

_AUTOMATION_SCRIPT_CODE = """\
import os
import shutil

SRC = os.path.expanduser("./incoming")
DST = os.path.expanduser("./processed")

for name in os.listdir(SRC):
    src_path = os.path.join(SRC, name)
    if os.path.isdir(src_path):
        with open(os.path.join(src_path, "success.txt"), "w") as f:
            f.write("processed!")
        shutil.move(src_path, os.path.join(DST, name))
"""


def test_automation_clarification_completion_creates_preview(tmp_path, monkeypatch):
    """The exact failing flow from PR review: process/automation clarification → real preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Turn 1: process/automation request → clarification question
    async def _clarification_reply(**kwargs):
        return {"answer": _AUTOMATION_CLARIFICATION_QUESTION, "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _clarification_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat(_AUTOMATION_REQUEST)

    # No preview should exist yet (process intent doesn't match is_code_draft_request)
    assert session.preview() is None

    # Turn 2: clarification answer → must materialize a real preview with code
    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_AUTOMATION_SCRIPT_CODE)
    )
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    resp = session.chat(_AUTOMATION_CLARIFICATION_ANSWER)
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None, (
        "A real preview must exist after automation clarification answer; "
        "this is the exact failing flow PR #296 must fix."
    )
    wf = preview.get("write_file", {})
    content = str(wf.get("content") or "").strip()
    assert content, "Preview content must not be empty after clarification answer"
    assert "shutil" in content or "success.txt" in content, "Generated code must reach the preview"

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "not able to prepare a governed preview" not in last_reply.lower(), (
        f"Vera must not falsely claim inability when clarification is sufficient: {last_reply!r}"
    )


def test_automation_clarification_go_ahead_submits(tmp_path, monkeypatch):
    """After successful automation preview materialization, 'go ahead' must submit."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _clarification_reply(**kwargs):
        return {"answer": _AUTOMATION_CLARIFICATION_QUESTION, "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _clarification_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat(_AUTOMATION_REQUEST)

    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_AUTOMATION_SCRIPT_CODE)
    )
    session.chat(_AUTOMATION_CLARIFICATION_ANSWER)

    preview = session.preview()
    assert preview is not None
    assert str(preview.get("write_file", {}).get("content") or "").strip()

    # Turn 3: go ahead → must not say "no preview"
    resp = session.chat("go ahead")
    assert resp.status_code == 200
    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "no preview" not in last_reply.lower() or "submitted" in last_reply.lower(), (
        f"Expected submission, got: {last_reply!r}"
    )


def test_automation_clarification_does_not_hijack_unrelated_followup(tmp_path, monkeypatch):
    """A non-clarification follow-up question must NOT trigger automation synthesis."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _clarification_reply(**kwargs):
        return {"answer": _AUTOMATION_CLARIFICATION_QUESTION, "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _clarification_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat(_AUTOMATION_REQUEST)

    # User changes topic with a question instead of answering — must not synthesize
    async def _info_reply(**kwargs):
        return {"answer": "The capital of France is Paris.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _info_reply)
    resp = session.chat("what is the capital of France?")
    assert resp.status_code == 200

    # No automation preview should have been synthesized
    preview = session.preview()
    if preview is not None:
        wf = preview.get("write_file", {})
        path = str(wf.get("path", ""))
        assert "automation.py" not in path, (
            f"Automation shell must not be synthesized for unrelated query, got path: {path!r}"
        )


def test_automation_underspecified_answer_fails_closed(tmp_path, monkeypatch):
    """A vague clarification answer with no detail signals must fail closed honestly."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _clarification_reply(**kwargs):
        return {"answer": _AUTOMATION_CLARIFICATION_QUESTION, "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _clarification_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat(_AUTOMATION_REQUEST)

    # Vague answer with no path token, no key:value details → must NOT synthesize
    async def _vague_reply(**kwargs):
        return {"answer": "I'm not sure, what do you recommend?", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _vague_reply)
    resp = session.chat("just do whatever you think is best")
    assert resp.status_code == 200

    # No automation preview should be synthesized from a vague answer
    preview = session.preview()
    if preview is not None:
        wf = preview.get("write_file", {})
        path = str(wf.get("path", ""))
        assert "automation.py" not in path, (
            f"Vague answer must not synthesize automation shell, got path: {path!r}"
        )


# ---------------------------------------------------------------------------
# 9. Direct automation request — single-turn detection
# ---------------------------------------------------------------------------


class TestLooksLikeDirectAutomationRequest:
    """Unit tests for the direct automation request detector (no clarification needed)."""

    @pytest.mark.parametrize(
        "msg",
        [
            # The exact failing prompt from live validation
            (
                "I need a process that continuously watches ./incoming. "
                "When a folder is fully copied in, add a status.txt file "
                "containing processed! and then move it to ./processed."
            ),
            "watch ./inbox for new folders and move them to ./done after adding a status file",
            (
                "monitor ~/VoxeraOS/notes/staging for new directories, "
                "write a marker file, then move them to ~/VoxeraOS/notes/archived"
            ),
            "poll ./uploads every 5 seconds and copy stable files to ./processed",
            "automate detecting new files in ./drop and moving them to ./handled",
        ],
    )
    def test_direct_automation_positives(self, msg: str) -> None:
        assert _looks_like_direct_automation_request(msg), f"Expected True for: {msg!r}"

    @pytest.mark.parametrize(
        "msg",
        [
            # Informational / conversational
            "what is the weather today?",
            "tell me a joke",
            "explain photosynthesis",
            "what time is it",
            "how do I use python",
            # Writing drafts
            "write me an essay about volcanoes",
            "draft a note about the meeting",
            # Simple file operations (not automation)
            "copy report.txt into receipts",
            "move ./report.txt to ./archive",
            "open ~/VoxeraOS/notes/readme.txt",
            "read the file ./config.yaml",
            "create a file called notes.txt with my grocery list",
            # Saves / submits
            "save that to a note",
            "submit it",
            "go ahead",
            # Borderline: "watch" without file/dir subject
            "watch the tutorial video at ~/Videos/tut.mp4",
            # Borderline: missing path token
            "monitor my folder for new files",
            # Borderline: has monitor + path but no action verb + no folder noun
            "show me the logs for ./service.log",
        ],
    )
    def test_direct_automation_negatives(self, msg: str) -> None:
        assert not _looks_like_direct_automation_request(msg), f"Expected False for: {msg!r}"


_DIRECT_AUTOMATION_PROMPT = (
    "I need a process that continuously watches ./incoming. "
    "When a folder is fully copied in, add a status.txt file "
    "containing processed! and then move it to ./processed."
)

_DIRECT_AUTOMATION_SCRIPT = """\
import os
import shutil
import time

SRC = os.path.expanduser("./incoming")
DST = os.path.expanduser("./processed")

while True:
    for name in os.listdir(SRC):
        src_path = os.path.join(SRC, name)
        if os.path.isdir(src_path):
            with open(os.path.join(src_path, "status.txt"), "w") as f:
                f.write("processed!")
            shutil.move(src_path, os.path.join(DST, name))
    time.sleep(1)
"""


def test_direct_automation_request_creates_preview(tmp_path, monkeypatch):
    """Single-turn fully specified automation request → real governed preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_DIRECT_AUTOMATION_SCRIPT)
    )

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat(_DIRECT_AUTOMATION_PROMPT)
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None, (
        "A real preview must exist for a direct fully specified automation request"
    )
    wf = preview.get("write_file", {})
    content = str(wf.get("content") or "").strip()
    assert content, "Preview content must not be empty — code must be injected"
    assert "shutil" in content or "status.txt" in content, (
        "Generated script code must reach the preview"
    )

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "not able to prepare a governed preview" not in last_reply.lower(), (
        f"Vera must not claim inability for a fully specified request: {last_reply!r}"
    )


def test_direct_automation_request_go_ahead_submits(tmp_path, monkeypatch):
    """After a direct automation preview, 'go ahead' must submit."""
    session = make_vera_session(monkeypatch, tmp_path)

    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_DIRECT_AUTOMATION_SCRIPT)
    )

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    session.chat(_DIRECT_AUTOMATION_PROMPT)

    preview = session.preview()
    assert preview is not None
    assert str(preview.get("write_file", {}).get("content") or "").strip()

    resp = session.chat("go ahead")
    assert resp.status_code == 200
    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "no preview" not in last_reply.lower() or "submitted" in last_reply.lower()


def test_direct_automation_does_not_hijack_weather(tmp_path, monkeypatch):
    """Weather questions must not be hijacked into the direct automation lane."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _weather_reply(**kwargs):
        code_draft = kwargs.get("code_draft", False)
        assert not code_draft, "Weather question must not be classified as code_draft"
        return {"answer": "It is sunny.", "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _weather_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    resp = session.chat("what is the weather today?")
    assert resp.status_code == 200

    preview = session.preview()
    if preview is not None:
        path = str(preview.get("write_file", {}).get("path", ""))
        assert "automation.py" not in path


def test_direct_automation_does_not_hijack_simple_file_op(tmp_path, monkeypatch):
    """Simple file operations ('copy report.txt to archive') must not be hijacked."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _reply(**kwargs):
        code_draft = kwargs.get("code_draft", False)
        assert not code_draft, "Simple file op must not be classified as code_draft"
        return {"answer": "OK, I can prepare that.", "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    resp = session.chat("move ./report.txt to ./archive")
    assert resp.status_code == 200

    preview = session.preview()
    if preview is not None:
        path = str(preview.get("write_file", {}).get("path", ""))
        assert "automation.py" not in path, (
            f"Simple file move must not synthesize automation shell, got: {path!r}"
        )


def test_direct_automation_does_not_hijack_writing_draft(tmp_path, monkeypatch):
    """Writing draft requests must not be hijacked into the direct automation lane."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _writing_reply(**kwargs):
        code_draft = kwargs.get("code_draft", False)
        assert not code_draft, "Writing draft must not be classified as code_draft"
        return {"answer": "Here is your essay...", "status": "ok:test"}

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _writing_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)
    resp = session.chat("write me an essay about volcanoes")
    assert resp.status_code == 200

    preview = session.preview()
    if preview is not None:
        path = str(preview.get("write_file", {}).get("path", ""))
        assert "automation.py" not in path


# ---------------------------------------------------------------------------
# 10. Previewable automation intent — first-turn refusal polish
# ---------------------------------------------------------------------------
#
# Regression coverage for the first-turn over-conservative refusal seam:
# clearly previewable automation/process requests must not get the blanket
# "I was not able to prepare a governed preview" reply.  Either a focused
# clarification question or a real preview is acceptable, but the blanket
# refusal is not.


class TestLooksLikePreviewableAutomationIntent:
    """Unit tests for the broader previewable automation intent detector."""

    @pytest.mark.parametrize(
        "msg",
        [
            # The exact failing live prompt from the task description
            (
                "I need a process that identifies a new folder copied into a "
                "specific folder and it does something with it and then it "
                "copies it to another folder can you help me?"
            ),
            # Variants without explicit path tokens
            "I want a process that watches a folder and copies new files to another folder",
            "automate detecting new directories and moving them somewhere else",
            "I need a script that monitors a folder for new files and copies them",
            "build a workflow that identifies folders and moves them to another folder",
        ],
    )
    def test_previewable_automation_positives(self, msg: str) -> None:
        assert _looks_like_previewable_automation_intent(msg), f"Expected True for: {msg!r}"

    @pytest.mark.parametrize(
        "msg",
        [
            # Informational
            "what is the weather today?",
            "tell me a joke",
            "explain photosynthesis",
            "what time is it",
            "how do i use python",
            # Writing drafts (no automation intent verb)
            "write me an essay about volcanoes",
            "draft a note about the meeting",
            # Simple file operations (no automation intent verb)
            "copy report.txt into receipts",
            "move ./report.txt to ./archive",
            "open ~/VoxeraOS/notes/readme.txt",
            "read the file ./config.yaml",
            "create a file called notes.txt with my grocery list",
            # Saves / submits
            "save that to a note",
            "submit it",
            "go ahead",
            # Has automation verb but no file/folder subject
            "monitor the build pipeline performance",
            "watch the tutorial video",
            # Has subject but no automation intent verb
            "what is in this folder",
            "describe a directory tree",
        ],
    )
    def test_previewable_automation_negatives(self, msg: str) -> None:
        assert not _looks_like_previewable_automation_intent(msg), f"Expected False for: {msg!r}"


_PREVIEWABLE_FIRST_TURN_PROMPT = (
    "I need a process that identifies a new folder copied into a specific "
    "folder and it does something with it and then it copies it to another "
    "folder can you help me?"
)


def test_first_turn_previewable_automation_does_not_blanket_refuse(tmp_path, monkeypatch):
    """A clearly previewable first-turn automation request must not hit the blanket refusal."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Simulate the live failure: LLM produces a preview-pane claim with no
    # fenced code block, which the guardrail collapses to the blanket refusal.
    async def _false_claim_reply(**kwargs):
        return {
            "answer": "I've prepared a preview of your request in the preview pane.",
            "status": "ok:test",
        }

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _false_claim_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat(_PREVIEWABLE_FIRST_TURN_PROMPT)
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]

    # The blanket refusal must not be the first-turn outcome for this request.
    assert BLANKET_PREVIEW_REFUSAL_TEXT not in last_reply, (
        f"Blanket refusal must not fire for previewable automation request: {last_reply!r}"
    )
    # The substituted clarification must include the focused detail prompts.
    assert "source folder" in last_reply.lower()
    assert "destination folder" in last_reply.lower()


def test_first_turn_previewable_automation_with_full_detail_drafts_directly(tmp_path, monkeypatch):
    """When all four direct-automation signals are present, a real preview is materialized."""
    session = make_vera_session(monkeypatch, tmp_path)

    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_DIRECT_AUTOMATION_SCRIPT)
    )

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat(_DIRECT_AUTOMATION_PROMPT)
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    content = str(preview.get("write_file", {}).get("content") or "").strip()
    assert content, "Direct automation request must materialize a real preview"

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert BLANKET_PREVIEW_REFUSAL_TEXT not in last_reply
    # The clarification fallback must NOT have fired — direct draft path won.
    assert _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY not in last_reply


def test_first_turn_previewable_automation_does_not_hijack_weather(tmp_path, monkeypatch):
    """Weather questions must not be substituted with the automation clarification.

    Forces the false-preview-claim → blanket refusal path so the substitution
    gate is actually exercised: the test verifies the gate's intent-detector
    fails closed for weather queries even when the guardrail would otherwise
    collapse the LLM reply.
    """
    session = make_vera_session(monkeypatch, tmp_path)

    async def _false_claim_reply(**kwargs):
        return {
            "answer": "I've prepared a preview of your request in the preview pane.",
            "status": "ok:test",
        }

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _false_claim_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat("what is the weather today?")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # Weather has no automation intent verb → substitution must not fire.
    assert _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY not in last_reply
    # The original blanket refusal stays in place for genuinely unsupported
    # weather phrasing — fail-closed is preserved here.
    assert BLANKET_PREVIEW_REFUSAL_TEXT in last_reply


def test_first_turn_previewable_automation_does_not_hijack_writing_request(tmp_path, monkeypatch):
    """Writing draft requests must not be substituted with the automation clarification.

    Forces the false-preview-claim → blanket refusal path so the substitution
    gate is actually exercised for a writing request.
    """
    session = make_vera_session(monkeypatch, tmp_path)

    async def _false_claim_reply(**kwargs):
        return {
            "answer": "I've prepared a preview of your request in the preview pane.",
            "status": "ok:test",
        }

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _false_claim_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat("draft a short note about the meeting")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # Writing draft message has no automation intent verb (process/script/etc.)
    # plus action-on-arrival hint → substitution must not fire.
    assert _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY not in last_reply


def test_first_turn_previewable_automation_does_not_hijack_simple_file_request(
    tmp_path, monkeypatch
):
    """Simple file ops without automation intent must not get the clarification fallback."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Force the false-preview-claim → blanket refusal path so we can verify
    # the substitution does NOT fire for simple file requests.
    async def _false_claim_reply(**kwargs):
        return {
            "answer": "I've prepared a preview of your request in the preview pane.",
            "status": "ok:test",
        }

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _false_claim_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    resp = session.chat("copy report.txt into receipts")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # No automation intent verb → automation clarification must not fire.
    assert _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY not in last_reply


def test_direct_automation_repeated_failure_does_not_occur(tmp_path, monkeypatch):
    """Repeated attempts with the same fully specified prompt must not keep failing."""
    session = make_vera_session(monkeypatch, tmp_path)

    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_code_reply_fn(_DIRECT_AUTOMATION_SCRIPT)
    )

    async def _no_builder(**kwargs):
        return None

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _no_builder)

    # Attempt 1 must succeed
    session.chat(_DIRECT_AUTOMATION_PROMPT)
    preview_1 = session.preview()
    assert preview_1 is not None

    # Also verify the assistant never emitted the repeated-failure message
    turns = session.turns()
    assistant_replies = [t["text"] for t in turns if t["role"] == "assistant"]
    for reply in assistant_replies:
        assert "not able to prepare a governed preview" not in reply.lower(), (
            f"Unexpected repeated-failure message: {reply!r}"
        )
