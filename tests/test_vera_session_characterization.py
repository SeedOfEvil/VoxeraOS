from __future__ import annotations

import json

from voxera.vera import service as vera_service
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session


def test_concise_answer_then_save_that_creates_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    assert session.chat("What is 2 + 2?").status_code == 200
    assert session.chat("save that to a note").status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["content"] == "2 + 2 is 4."
    assert preview["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")


def test_concise_answer_then_thanks_then_save_that_keeps_meaningful_answer(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "You're welcome!", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("What is 2 + 2?")
    session.chat("thanks")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["content"] == "2 + 2 is 4."
    assert "welcome" not in preview["write_file"]["content"].lower()


def test_explanation_then_save_that_creates_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "Explain photosynthesis simply.":
            return {
                "answer": (
                    "Photosynthesis lets plants use sunlight, water, and carbon dioxide to make sugar."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("Explain photosynthesis simply.")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "sunlight" in preview["write_file"]["content"].lower()
    assert "carbon dioxide" in preview["write_file"]["content"].lower()


def test_active_preview_rename_path_revision_and_submit_remain_truthful(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "Explain photosynthesis simply.":
            return {
                "answer": "Photosynthesis lets plants turn sunlight into stored food energy.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("Explain photosynthesis simply.")
    session.chat("save that to a note")
    session.chat("call the note math.txt")
    session.chat("save it as math.txt")
    session.chat("use path: ~/VoxeraOS/notes/math.txt")

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"
    assert vera_service.read_session_preview(session.queue, session.session_id) is None


def test_checklist_request_returns_conversational_answer_not_preview_error(tmp_path, monkeypatch):
    """Checklist/planning requests must be answered conversationally,
    not routed through preview drafting."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_answer = (
        "Here's your wedding prep checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": checklist_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "create a checklist would surely help on the many things I need to do. "
        "First I need to find a +1, I also need to get a nice suit, "
        "I need to get the tickets to travel there and accommodations "
        "and I need to take time off work!"
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    # Must contain the actual checklist, not a preview error
    assert "checklist" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert "couldn't safely prepare" not in last_turn["text"].lower()
    # No preview should have been created
    assert session.preview() is None


def test_checklist_answer_then_save_that_creates_preview(tmp_path, monkeypatch):
    """After a checklist answer, 'save that' should create a governed preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_answer = (
        "Here's your wedding prep checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        if "checklist" in user_message.lower():
            return {"answer": checklist_answer, "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("create a checklist for my wedding prep")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "write_file" in preview
    assert "checklist" in preview["write_file"]["content"].lower()


def test_planning_request_returns_conversational_answer(tmp_path, monkeypatch):
    """Planning/step-by-step requests must be answered conversationally."""
    session = make_vera_session(monkeypatch, tmp_path)

    plan_answer = (
        "Here's your plan for the trip:\n\n1. Book flights\n2. Reserve hotel\n3. Plan activities"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": plan_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan for a vacation to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    assert "plan" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_preview_claim_language_not_blocked(tmp_path, monkeypatch):
    """Even if the LLM uses 'I've prepared' phrasing, checklist turns must not
    be blocked by the false-preview-claim guardrail."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_claim_phrasing = (
        "I've prepared your checklist:\n\n1. Find a plus-one\n2. Buy a suit\n3. Book travel"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": answer_with_claim_phrasing, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "plus-one" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert "couldn't safely prepare" not in last_turn["text"].lower()


def test_brainstorm_request_returns_conversational_answer(tmp_path, monkeypatch):
    """Brainstorming requests should be answer-first."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Here are some ideas:\n- idea A\n- idea B\n- idea C",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("brainstorm what I need for the camping trip")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "idea" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()


def test_save_checklist_to_file_does_not_bypass_preview(tmp_path, monkeypatch):
    """'save a checklist to a file' has explicit save intent — should NOT be
    treated as answer-first."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Understood. Nothing has been submitted or executed yet. I can send it whenever you're ready.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("save a checklist to a file called wedding.txt")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Must NOT produce a raw conversational checklist — the save+file intent
    # means this should go through preview drafting, not answer-first.
    assert last_turn["role"] == "assistant"
    assert "1." not in last_turn["text"]
    assert "plus-one" not in last_turn["text"].lower()


def test_checklist_request_with_active_preview_does_not_bypass(tmp_path, monkeypatch):
    """When a governed preview is already active, a checklist-style message
    should NOT trigger answer-first bypass — the preview context dominates."""
    session = make_vera_session(monkeypatch, tmp_path)

    preview = {
        "goal": "write a file called notes.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/notes.txt",
            "content": "existing content",
            "mode": "overwrite",
        },
    }
    vera_service.write_session_preview(session.queue, session.session_id, preview)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Understood. I still have the current request ready.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("add a checklist to the file")
    assert res.status_code == 200

    # Preview must still be intact (not cleared by answer-first bypass).
    # The builder may have updated content, but the preview itself persists.
    assert session.preview() is not None
    assert "write_file" in session.preview()


def test_unsafe_path_revision_fails_closed_and_preserves_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    preview = {
        "goal": "write a file called math.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/math.txt",
            "content": "2 + 2 is 4.",
            "mode": "overwrite",
        },
    }
    vera_service.write_session_preview(session.queue, session.session_id, preview)

    res = session.chat("use path: ~/VoxeraOS/notes/../bad.txt")

    assert res.status_code == 200
    assert vera_service.read_session_preview(session.queue, session.session_id) == preview
    assert session.turns()[-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Bug 1: false preview claims stripped from conversational answers
# ---------------------------------------------------------------------------


def test_checklist_answer_with_preview_pane_claim_is_sanitized(tmp_path, monkeypatch):
    """If the LLM says 'You can see the draft in the preview pane' during an
    answer-first checklist turn, that sentence must be stripped — but the
    checklist content itself must be preserved."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_pane_claim = (
        "I've put together a checklist for you.\n"
        "You can see the draft in the preview pane.\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer_with_pane_claim, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    # Checklist content preserved
    assert "plus-one" in last_turn["text"].lower()
    assert "nice suit" in last_turn["text"].lower()
    # False preview-pane claim removed
    assert "preview pane" not in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    # No real preview created
    assert session.preview() is None


def test_checklist_answer_without_preview_claim_passes_through(tmp_path, monkeypatch):
    """A clean checklist answer (no preview-pane language) must pass through
    unmodified."""
    session = make_vera_session(monkeypatch, tmp_path)

    clean_answer = (
        "Here's your checklist:\n\n1. Find a plus-one\n2. Get a nice suit\n3. Book travel"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": clean_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["text"] == clean_answer


# ---------------------------------------------------------------------------
# Bug 2: multi-turn planning/checklist continuation stays answer-first
# ---------------------------------------------------------------------------


def test_multi_turn_checklist_clarification_then_details_stays_answer_first(tmp_path, monkeypatch):
    """When Vera asks for more details after a checklist request and the user
    provides them, the follow-up turn must remain answer-first (no preview
    failure)."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! What are the main things you need to get done?",
                "status": "ok:test",
            }
        # Follow-up with details — should be answered conversationally
        return {
            "answer": (
                "Here's your checklist:\n\n"
                "1. Find a plus-one\n"
                "2. Get a nice suit\n"
                "3. Book tickets and accommodations\n"
                "4. Take time off work"
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    # Turn 1: vague checklist request
    res1 = session.chat("create a checklist would surely help on the many things I need to do.")
    assert res1.status_code == 200

    # Turn 2: user provides details (no planning keywords)
    res2 = session.chat(
        "First I need to find a +1, I also need to get a nice suit, "
        "I need to get the tickets to travel there and accommodations "
        "and I need to take time off work!"
    )
    assert res2.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    # Must contain the actual checklist answer, not a preview error
    assert "checklist" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert "couldn't safely prepare" not in last_turn["text"].lower()
    # No preview created
    assert session.preview() is None


def test_multi_turn_planning_then_save_that_creates_preview(tmp_path, monkeypatch):
    """After a multi-turn planning flow, 'save that' must create a governed
    preview from the most recent conversational answer."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_answer = (
        "Here's your checklist:\n\n1. Find a plus-one\n2. Get a nice suit\n3. Book travel"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! What are the main things you need to get done?",
                "status": "ok:test",
            }
        if "plus" in user_message.lower() or "suit" in user_message.lower():
            return {"answer": checklist_answer, "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("create a checklist for my wedding prep")
    session.chat("I need to find a +1, get a suit, and book travel")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "write_file" in preview
    assert "checklist" in preview["write_file"]["content"].lower()


def test_checklist_answer_then_send_without_save_is_truthful(tmp_path, monkeypatch):
    """After a conversational checklist answer with no 'save that', 'send it'
    must truthfully report that no preview exists."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Here's your checklist:\n\n1. Item A\n2. Item B",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("make me a checklist for the trip")
    res = session.chat("send it")
    assert res.status_code == 200

    # No preview was created, so send-it should be truthful
    assert session.preview() is None


def test_planning_continuation_clears_when_save_intent_detected(tmp_path, monkeypatch):
    """If a follow-up turn after planning has save/write intent, it should NOT
    stay in the answer-first lane — it should go through normal preview
    drafting."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        return {
            "answer": "Sure! What should the checklist include?",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    # Turn 1: planning request → sets continuation flag
    session.chat("help me plan a vacation")

    # Turn 2: follow-up WITH save intent → must NOT be answer-first
    res = session.chat("save a checklist to a file called vacation.txt")
    assert res.status_code == 200

    # The save intent should have been detected, so the turn is NOT answer-first.
    # The continuation flag should now be cleared.
    from voxera.vera.session_store import read_session_conversational_planning_active

    assert not read_session_conversational_planning_active(session.queue, session.session_id)


# ---------------------------------------------------------------------------
# Comprehensive preview-truth and submission-truth sanitization
# ---------------------------------------------------------------------------


def test_checklist_answer_with_submission_claim_is_sanitized(tmp_path, monkeypatch):
    """If the LLM falsely claims it submitted a checklist to the queue,
    that claim must be stripped while preserving checklist content."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_submission = (
        "Here's your checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n\n"
        "I've submitted that checklist to the queue for you."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer_with_submission, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Checklist content preserved
    assert "plus-one" in last_turn["text"].lower()
    assert "nice suit" in last_turn["text"].lower()
    # False submission claim removed
    assert "submitted" not in last_turn["text"].lower()
    assert "queue" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_answer_with_preview_update_claim_is_sanitized(tmp_path, monkeypatch):
    """Preview-update phrases like 'I've prepared a draft' must be stripped
    from answer-first turns."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_draft_claim = (
        "I've prepared a draft for you.\n\n"
        "Here's your checklist:\n\n"
        "1. Book flights\n"
        "2. Reserve hotel\n"
        "3. Plan activities"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer_with_draft_claim, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan for a vacation to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Checklist content preserved
    assert "book flights" in last_turn["text"].lower()
    assert "reserve hotel" in last_turn["text"].lower()
    # False draft claim removed
    assert "prepared a draft" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_answer_with_json_blob_is_sanitized(tmp_path, monkeypatch):
    """JSON/VoxeraOS payload blobs must be stripped from conversational
    checklist answers."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_json = (
        "Here's your checklist:\n\n"
        "1. Buy tickets\n"
        "2. Pack bags\n\n"
        '```json\n{"goal": "create checklist", "write_file": '
        '{"path": "~/notes/list.md"}}\n```'
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer_with_json, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the trip")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Checklist content preserved
    assert "buy tickets" in last_turn["text"].lower()
    assert "pack bags" in last_turn["text"].lower()
    # JSON blob removed
    assert "```json" not in last_turn["text"].lower()
    assert '"goal"' not in last_turn["text"]
    assert session.preview() is None


def test_send_it_without_preview_after_checklist_is_truthful(tmp_path, monkeypatch):
    """'send it' after a checklist answer (no prior 'save that') must
    truthfully report no preview exists — no fake submission."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Here's your checklist:\n\n1. Item A\n2. Item B",
                "status": "ok:test",
            }
        # LLM might hallucinate a submission
        return {
            "answer": "I've sent it to the queue!",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("make me a checklist for work")
    res = session.chat("send it")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Must NOT claim submission happened
    assert "sent it to the queue" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_output_is_plain_text_not_json(tmp_path, monkeypatch):
    """Checklist output must be plain text/markdown — never raw JSON."""
    session = make_vera_session(monkeypatch, tmp_path)

    plain_checklist = (
        "Here's your wedding prep checklist:\n\n"
        "- [ ] Find a plus-one\n"
        "- [ ] Get a nice suit\n"
        "- [ ] Book travel\n"
        "- [ ] Request time off"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": plain_checklist, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "yes can you make a checklist of the following: take time off, buy the tickets, get in shape"
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Must be plain text checklist, not JSON
    assert "- [ ]" in last_turn["text"] or "1." in last_turn["text"]
    assert '"intent"' not in last_turn["text"]
    assert '"goal"' not in last_turn["text"]
    assert "preview pane" not in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert session.preview() is None


# ---------------------------------------------------------------------------
# Broken case 1 & 2: broader preview/submission language sanitization
# ---------------------------------------------------------------------------


def test_checklist_answer_with_take_a_look_at_preview_is_sanitized(tmp_path, monkeypatch):
    """'Take a look at the preview' must be stripped — broader than just
    'in the preview pane'."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your checklist:\n\n"
        "1. Take time off\n"
        "2. Buy the tickets\n\n"
        "Take a look at the preview to review the structure."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist: take time off, buy the tickets")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "take time off" in last_turn["text"].lower()
    assert "the preview" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_answer_with_ill_submit_is_sanitized(tmp_path, monkeypatch):
    """'I'll submit it' and 'I can submit' must be stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n\n"
        "I'll submit it to the system queue whenever you're ready."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "plus-one" in last_turn["text"].lower()
    assert "submit" not in last_turn["text"].lower()
    assert "system queue" not in last_turn["text"].lower()
    assert session.preview() is None


def test_multi_turn_planning_with_draft_language_is_sanitized(tmp_path, monkeypatch):
    """Multi-turn: Vera asks for details, user provides them, LLM answer
    mentions 'the draft' — must be stripped, content preserved."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! What things do you need to get done?",
                "status": "ok:test",
            }
        return {
            "answer": (
                "I've updated the draft with your items.\n\n"
                "1. Find a plus-one\n"
                "2. Get a nice suit\n"
                "3. Book travel"
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("create a checklist would surely help on the many things I need to do.")
    res = session.chat("I need to find a +1, get a suit, and book travel")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "plus-one" in last_turn["text"].lower() or "find a" in last_turn["text"].lower()
    assert "the draft" not in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert session.preview() is None


# ---------------------------------------------------------------------------
# Broken case 3: explicit save intent for checklist must create preview
# ---------------------------------------------------------------------------


def test_save_checklist_to_note_creates_preview(tmp_path, monkeypatch):
    """'save a checklist to a note for my wedding prep' must produce a
    governed preview — not fail with 'I was not able to prepare'."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_content = (
        "Wedding Prep Checklist\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": checklist_content, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("save a checklist to a note for my wedding prep")
    assert res.status_code == 200

    # A governed preview must exist with the checklist content
    preview = session.preview()
    assert preview is not None, "Expected a governed preview for save-intent checklist"
    assert "write_file" in preview
    assert "checklist" in preview["write_file"]["content"].lower()

    # The assistant text must NOT contain the preview failure message
    last_turn = session.turns()[-1]
    assert "was not able to prepare" not in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
