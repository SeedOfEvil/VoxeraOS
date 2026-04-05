from __future__ import annotations

import json
import re

from voxera.vera import session_store as vera_session_store
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


def test_concise_answer_then_save_that_as_named_file_preserves_content(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("What is 2 + 2?")
    session.chat("save that as math.txt")

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"
    assert preview["write_file"]["content"] == "2 + 2 is 4."


def test_save_previous_content_repairs_empty_preview_content(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "Give me a concise summary.":
            return {"answer": "Concise summary with key point A and B.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("Give me a concise summary.")
    vera_session_store.write_session_preview(
        session.queue,
        session.session_id,
        {
            "goal": "write a file called SA.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/SA.txt",
                "content": "",
                "mode": "overwrite",
            },
        },
    )

    session.chat("save previous content")
    repaired = session.preview()
    assert repaired is not None
    assert repaired["write_file"]["path"] == "~/VoxeraOS/notes/SA.txt"
    assert repaired["write_file"]["content"] == "Concise summary with key point A and B."


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
    assert vera_session_store.read_session_preview(session.queue, session.session_id) is None


def test_save_as_flow_ignores_prior_linked_completion_status_text(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        return {"answer": f"reply: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    vera_session_store.append_session_turn(
        session.queue,
        session.session_id,
        role="assistant",
        text=(
            "Your linked goal job completed successfully. Wrote text to "
            "~/VoxeraOS/notes/older-note.txt."
        ),
    )

    res = session.chat("tell me a funny joke and save it as superfunny.txt")
    assert res.status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/superfunny.txt"
    assert (
        preview["write_file"]["content"]
        == "reply: tell me a funny joke and save it as superfunny.txt"
    )
    assert "wrote text to" not in preview["write_file"]["content"].lower()


def test_active_text_preview_content_updates_on_clear_generation_followup(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me a hilarious joke":
            return {"answer": "Fresh hilarious joke body for this draft only.", "status": "ok:test"}
        return {"answer": "Initial answer content.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("what is 2 + 2?")
    session.chat("save that as draft.txt")
    before = session.preview()
    assert before is not None
    assert before["write_file"]["content"] == "Initial answer content."

    update = session.chat("tell me a hilarious joke")
    assert update.status_code == 200
    after = session.preview()
    assert after is not None
    assert after["write_file"]["path"] == "~/VoxeraOS/notes/draft.txt"
    assert after["write_file"]["content"] == "Fresh hilarious joke body for this draft only."


def test_ambiguous_active_preview_content_replacement_fails_closed(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "I can help with that.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("What is 2 + 2?")
    session.chat("save that as ambiguous.txt")
    preview_before = session.preview()
    assert preview_before is not None

    response = session.chat("replace content with that")
    assert response.status_code == 200
    preview_after = session.preview()
    assert preview_after == preview_before
    last_turn = session.turns()[-1]["text"].lower()
    assert "left the active draft content unchanged" in last_turn
    assert "ambiguous" in last_turn


def test_combined_generation_save_named_note_uses_actual_joke_not_control_ack(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me a hilarious joke and save it to a note called jokie.txt":
            return {
                "answer": (
                    "Why did the scarecrow get promoted? Because he was outstanding in his field."
                ),
                "status": "ok:test",
            }
        return {
            "answer": (
                "Done. I've updated the draft for `jokie.txt` with a fresh joke. "
                "Let me know when you're ready to save it."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("tell me a hilarious joke and save it to a note called jokie.txt")
    assert res.status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokie.txt"
    content = preview["write_file"]["content"].lower()
    assert "outstanding in his field" in content
    assert "i've updated the draft" not in content
    assert "nothing has been submitted" not in content
    assert "i still have the current request ready" not in content


def test_combined_generation_save_filters_wrapper_text_and_keeps_only_joke_content(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me a dad joke and save it as dadjoke.txt":
            return {
                "answer": (
                    'I added a new joke ("I\'m afraid for the calendar. Its days are numbered.") '
                    "to the file content.\n\n"
                    "You can see the current draft in the preview pane; let me know if you'd like "
                    "to change the joke or the filename before we submit it."
                ),
                "status": "ok:test",
            }
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("tell me a dad joke and save it as dadjoke.txt")
    assert res.status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/dadjoke.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert lowered == "i'm afraid for the calendar. its days are numbered."
    assert "i added a new joke" not in lowered
    assert "you can see the current draft" not in lowered
    assert "nothing has been submitted or executed yet" not in lowered
    assert "i’m ready to submit this to the queue whenever you’re set" not in lowered


def test_followup_tell_another_joke_and_add_as_content_refreshes_preview_content(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me another joke and add as content":
            return {
                "answer": "I told my keyboard I needed space; it said, 'I’m already on it.'",
                "status": "ok:test",
            }
        return {"answer": "Seed joke for the first draft content.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("what is 2 + 2?")
    session.chat("save that as jokie.txt")
    baseline = session.preview()
    assert baseline is not None
    assert baseline["write_file"]["path"] == "~/VoxeraOS/notes/jokie.txt"

    followup = session.chat("tell me another joke and add as content")
    assert followup.status_code == 200
    updated = session.preview()
    assert updated is not None
    assert updated["write_file"]["path"] == "~/VoxeraOS/notes/jokie.txt"
    assert "keyboard i needed space" in updated["write_file"]["content"].lower()
    assert "updated the draft" not in updated["write_file"]["content"].lower()


def test_active_draft_refresh_with_wrapper_reply_replaces_body_with_pure_new_joke(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me a dad joke and save it as dadjoke.txt":
            return {
                "answer": "Why did the coffee file a police report? It got mugged.",
                "status": "ok:test",
            }
        if user_message == "tell me a different joke and add it as content":
            return {
                "answer": (
                    "I added a new joke (\"Why don't skeletons fight each other? "
                    "They don't have the guts.\") to the file content.\n\n"
                    "You can see the current draft in the preview pane; let me know if you'd like "
                    "to change the joke or the filename before we submit it."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    first = session.chat("tell me a dad joke and save it as dadjoke.txt")
    assert first.status_code == 200
    baseline = session.preview()
    assert baseline is not None
    assert baseline["write_file"]["path"] == "~/VoxeraOS/notes/dadjoke.txt"
    assert "coffee file a police report" in baseline["write_file"]["content"].lower()

    second = session.chat("tell me a different joke and add it as content")
    assert second.status_code == 200
    updated = session.preview()
    assert updated is not None
    assert updated["write_file"]["path"] == "~/VoxeraOS/notes/dadjoke.txt"
    refreshed = updated["write_file"]["content"].lower()
    assert "don't skeletons fight each other" in refreshed
    assert "coffee file a police report" not in refreshed
    assert "i added a new joke" not in refreshed
    assert "you can see the current draft" not in refreshed
    assert "nothing has been submitted or executed yet" not in refreshed
    assert "i’m ready to submit this to the queue whenever you’re set" not in refreshed


def test_active_draft_refresh_unquoted_wrapper_with_contractions_keeps_full_joke(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me an astronaut joke and save it as astrojoke.txt":
            return {
                "answer": "Why did the astronaut break up with the moon? It needed space.",
                "status": "ok:test",
            }
        if user_message == "tell me a different joke and add it as content":
            return {
                "answer": (
                    "Updated the draft preview with a fresh joke.\n\n"
                    "Why don't skeletons fight each other? They don't have the guts.\n\n"
                    "You can review the content in the preview pane and submit it whenever you're ready."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    first = session.chat("tell me an astronaut joke and save it as astrojoke.txt")
    assert first.status_code == 200

    second = session.chat("tell me a different joke and add it as content")
    assert second.status_code == 200
    updated = session.preview()
    assert updated is not None
    assert updated["write_file"]["path"] == "~/VoxeraOS/notes/astrojoke.txt"
    refreshed = updated["write_file"]["content"].lower()
    assert refreshed == "why don't skeletons fight each other? they don't have the guts."
    assert refreshed.startswith("why don't skeletons fight each other?")
    assert refreshed.endswith("they don't have the guts.")
    assert "updated the draft preview" not in refreshed
    assert "you can review the content" not in refreshed


def test_single_turn_generate_save_poem_uses_same_turn_authored_body(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    poem = (
        "We sail on quiet starlight streams,\n"
        "Past silver dust and engine dreams,\n"
        "Where midnight hums in violet tone,\n"
        "And every orbit feels like home."
    )

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "write a short poem about space and save it as spacepoem.txt":
            return {"answer": poem, "status": "ok:test"}
        if user_message == "write a short poem and save it as poem.txt":
            return {
                "answer": (
                    "Ash drifts softly where old fire slept,\n"
                    "Stone remembers promises it kept.\n\n"
                    "You can review the content in the preview pane and submit it whenever you're ready."
                ),
                "status": "ok:test",
            }
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("write a short poem about space and save it as spacepoem.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/spacepoem.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert "starlight streams" in lowered
    assert "i couldn't resolve a suitable recent assistant-authored summary/answer" not in lowered
    assert "nothing has been submitted" not in lowered
    assert "ready to submit" not in lowered

    second = session.chat("write a short poem and save it as poem.txt")
    assert second.status_code == 200
    poem_preview = session.preview()
    assert poem_preview is not None
    assert poem_preview["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
    poem_body = poem_preview["write_file"]["content"].lower()
    assert "ash drifts softly where old fire slept" in poem_body
    assert "you can review the content in the preview pane" not in poem_body
    assert "ready" not in poem_body


def test_single_turn_generate_save_poem_strips_if_happy_helper_tail(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "write a short poem and save it as poem.txt":
            return {
                "answer": (
                    "Ash drifts softly where old fire slept,\n"
                    "Stone remembers promises it kept.\n\n"
                    "If you're happy with how it looks, just let me know or click submit to save it.\n"
                    "If that looks good, just hit Submit to save the file."
                ),
                "status": "ok:test",
            }
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("write a short poem and save it as poem.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert lowered.startswith("ash drifts softly where old fire slept")
    assert lowered.endswith("stone remembers promises it kept.")
    assert "if you're happy with how it looks" not in lowered
    assert "click submit to save it" not in lowered
    assert "if that looks good" not in lowered
    assert "just hit submit to save the file" not in lowered

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["content"] == content


def test_single_turn_generate_save_mauna_loa_summary_keeps_full_body_and_submit_truth(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)
    summary = (
        "Mauna Loa is one of Earth's largest active volcanoes and has erupted repeatedly in recorded "
        "history, shaping Hawaii's landscape and serving as a major site for atmospheric monitoring."
    )

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "give me a short summary of Mauna Loa and save it as maunaloa.txt":
            return {
                "answer": (
                    "I've drafted a short summary for you and prepared a preview to save it as `maunaloa.txt`.\n\n"
                    f"{summary}\n\n"
                    "Nothing has been submitted or executed yet. "
                    "I'm ready to submit this to the queue whenever you're set."
                ),
                "status": "ok:test",
            }
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("give me a short summary of Mauna Loa and save it as maunaloa.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/maunaloa.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert content == summary
    assert lowered.startswith("mauna loa is one of earth's largest active volcanoes")
    assert lowered.endswith("serving as a major site for atmospheric monitoring.")
    assert "i've drafted a short summary for you" not in lowered
    assert "nothing has been submitted or executed yet" not in lowered
    assert "i'm ready to submit this to the queue whenever you're set" not in lowered

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/maunaloa.txt"
    assert payload["write_file"]["content"] == content


def test_single_turn_generate_save_summary_ignores_preview_pane_meta_narration(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)
    summary = (
        "Mauna Loa is one of Earth's largest active volcanoes and remains active, with eruptions that "
        "have shaped Hawaii and provided long-running atmospheric observations."
    )

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "give me a short summary of Mauna Loa and save it as maunaloa.txt":
            return {
                "answer": (
                    "I've staged a request in the preview pane for `maunaloa.txt`.\n\n"
                    "Please review the content and submit when you're ready.\n\n"
                    f"{summary}"
                ),
                "status": "ok:test",
            }
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("give me a short summary of Mauna Loa and save it as maunaloa.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/maunaloa.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert content == summary
    assert "i've staged a request" not in lowered
    assert "please review the content" not in lowered
    assert lowered.startswith("mauna loa is one of earth's largest active volcanoes")

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/maunaloa.txt"
    assert payload["write_file"]["content"] == content


def test_single_turn_generate_save_joke_strips_explanatory_tail_and_submit_truthful(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "tell me an astronaut joke and save it as astrojoke.txt":
            return {
                "answer": (
                    "Why did the astronaut break up with the moon? "
                    "It needed space.\n\n"
                    "I've drafted a plan to save that joke to `astrojoke.txt` for you. "
                    "Nothing has been submitted or executed yet. I'm ready to submit whenever you are."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("tell me an astronaut joke and save it as astrojoke.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/astrojoke.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert lowered.startswith("why did the astronaut break up with the moon?")
    assert "it needed space." in lowered
    assert "i've drafted a plan" not in lowered
    assert "nothing has been submitted" not in lowered
    assert "ready to submit" not in lowered

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/astrojoke.txt"
    assert payload["write_file"]["content"] == content
    assert "i've drafted a plan" not in payload["write_file"]["content"].lower()


def test_single_turn_generate_save_volcano_fact_does_not_require_prior_artifact(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    fact = "Volcano fact: Magma is called lava only after it reaches Earth's surface."

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "give me a short volcano fact and save it as volcanofact.txt":
            return {"answer": fact, "status": "ok:test"}
        return {"answer": "fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("give me a short volcano fact and save it as volcanofact.txt")
    assert res.status_code == 200
    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/volcanofact.txt"
    content = preview["write_file"]["content"]
    lowered = content.lower()
    assert "magma is called lava only after it reaches earth's surface" in lowered
    assert "i couldn't resolve a suitable recent assistant-authored summary/answer" not in lowered

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/volcanofact.txt"
    assert payload["write_file"]["content"] == content


def test_combined_generate_save_content_type_matrix_prefers_authored_body_not_control_text(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    responses = {
        "tell me a short joke and save it as matrix-joke.txt": (
            "I've drafted a short joke for you and prepared a preview to save it as `matrix-joke.txt`.\n\n"
            "Why don't scientists trust atoms? Because they make up everything."
        ),
        "write a short poem and save it as matrix-poem.txt": (
            "I've drafted a short poem for you and prepared a preview to save it as `matrix-poem.txt`.\n\n"
            "Rain taps softly on the street,\n"
            "Night and neon gently meet."
        ),
        "give me a short volcano fact and save it as matrix-fact.txt": (
            "I've drafted a short fact for you and prepared a preview to save it as `matrix-fact.txt`.\n\n"
            "Volcano fact: Most volcanoes form where tectonic plates meet."
        ),
        "give me a short climate summary and save it as matrix-summary.txt": (
            "I've drafted a short summary for you and prepared a preview to save it as `matrix-summary.txt`.\n\n"
            "Climate summary: Global temperatures are trending upward over recent decades."
        ),
    }

    async def _fake_reply(*, turns, user_message):
        _ = turns
        return {"answer": responses[user_message], "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    expectations = {
        "matrix-joke.txt": "scientists trust atoms",
        "matrix-poem.txt": "rain taps softly",
        "matrix-fact.txt": "most volcanoes form",
        "matrix-summary.txt": "global temperatures are trending upward",
    }

    for message, expected_fragment in [
        ("tell me a short joke and save it as matrix-joke.txt", expectations["matrix-joke.txt"]),
        ("write a short poem and save it as matrix-poem.txt", expectations["matrix-poem.txt"]),
        (
            "give me a short volcano fact and save it as matrix-fact.txt",
            expectations["matrix-fact.txt"],
        ),
        (
            "give me a short climate summary and save it as matrix-summary.txt",
            expectations["matrix-summary.txt"],
        ),
    ]:
        res = session.chat(message)
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        body = preview["write_file"]["content"].lower()
        assert expected_fragment in body
        assert "i've drafted a short" not in body
        assert "prepared a preview" not in body
        assert "ready to save" not in body


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
    vera_session_store.write_session_preview(session.queue, session.session_id, preview)

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
    vera_session_store.write_session_preview(session.queue, session.session_id, preview)

    res = session.chat("use path: ~/VoxeraOS/notes/../bad.txt")

    assert res.status_code == 200
    assert vera_session_store.read_session_preview(session.queue, session.session_id) == preview
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


def _assert_conversational_checklist_contract(text: str) -> None:
    lowered = text.lower()
    assert re.search(r"(?m)^(?:- |\d+[.)] )", text), text
    for banned in ("preview", "draft", "submit", "submission", "queue", "queued"):
        assert banned not in lowered, text
    assert "```json" not in lowered
    assert '{"' not in text
    meta_only_markers = (
        "i organized",
        "i've grouped",
        "i organized it",
        "does this look right",
        "take a look",
        "let me know when",
    )
    assert not any(marker in lowered for marker in meta_only_markers), text


def test_wedding_checklist_determinism_with_meta_only_or_json_outputs(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)
    responses = [
        "I've grouped everything logically. Does this look right before we save?",
        ('{"intent":"checklist","items":["Choose venue","Book photographer","Send invites"]}'),
        (
            "You can review the draft in the preview pane.\n"
            "1. Confirm budget\n2. Build guest list\n3. Reserve venue"
        ),
    ]
    call_count = {"value": 0}

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        index = call_count["value"] % len(responses)
        call_count["value"] += 1
        return {"answer": responses[index], "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    for _ in range(9):
        session.chat("create a checklist for my wedding prep")
        _assert_conversational_checklist_contract(session.turns()[-1]["text"])
        assert session.preview() is None


def test_grocery_checklist_determinism_repeated(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)
    responses = [
        "I've put this together for you. Take a look and let me know when to save it.",
        '```json\n{"items": ["Milk", "Eggs", "Spinach", "Rice"]}\n```',
        "- Milk\n- Eggs\n- Spinach\n- Rice",
    ]
    calls = {"value": 0}

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        reply = responses[calls["value"] % len(responses)]
        calls["value"] += 1
        return {"answer": reply, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    for _ in range(8):
        session.chat("make me a grocery checklist for this week")
        _assert_conversational_checklist_contract(session.turns()[-1]["text"])
        assert session.preview() is None


def test_two_turn_planning_determinism_blocks_preview_language_and_json(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)
    responses = [
        "Sure — what details should I include?",
        '{"goal":"trip planning","tasks":["Book flights","Reserve hotel","Plan activities"]}',
        "I've organized it logically and updated the draft in the preview.",
    ]
    calls = {"value": 0}

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        reply = responses[calls["value"] % len(responses)]
        calls["value"] += 1
        return {"answer": reply, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("help me plan a wedding weekend checklist")
    for _ in range(6):
        session.chat("I need to handle travel, vendors, and a day-of timeline")
        _assert_conversational_checklist_contract(session.turns()[-1]["text"])
        assert session.preview() is None


def test_wedding_checklist_preserves_user_items_over_generic_fallback(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)
    noisy = (
        "create_file wedding_prep_checklist.md\n"
        "I've grouped this in the preview pane and draft state."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        return {"answer": noisy, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    prompt = (
        "yes can you make a checklist of the following: take time off, buy the tickets, "
        "get in shape, buy the suit, call mom, tell my friend, "
        "be the best man at a wedding, rent a car during the trip, pack."
    )
    for _ in range(5):
        session.chat(prompt)
        text = session.turns()[-1]["text"].lower()
        for expected in (
            "take time off",
            "buy the tickets",
            "get in shape",
            "buy the suit",
            "call mom",
            "tell my friend",
            "be the best man at a wedding",
            "rent a car during the trip",
            "pack",
        ):
            assert expected in text
        assert "finalize guest list" not in text
        assert "create_file" not in text
        assert ".md" not in text


def test_grocery_checklist_preserves_requested_items_not_generic_boilerplate(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        return {"answer": "I updated the draft in preview.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    for _ in range(5):
        session.chat(
            "make a checklist. I need coffee, rice, apples and bannanas, bread, chess, ham and some pasta."
        )
        text = session.turns()[-1]["text"].lower()
        for expected in (
            "coffee",
            "rice",
            "apples",
            "bannanas",
            "bread",
            "chess",
            "ham",
            "some pasta",
        ):
            assert expected in text
        assert "define the goal and outcome" not in text
        assert "break the work into concrete steps" not in text


def test_two_turn_planning_preserves_second_turn_user_details(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        if "checklist" in user_message.lower():
            return {"answer": "Sure, what details should I include?", "status": "ok:test"}
        return {"answer": "Here's a draft in preview.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    session.chat("create a checklist would surely help on the many things I need to do.")
    session.chat(
        "First I need to find a +1, I also need to get a nice suit, "
        "I need to get the tickets to travel there and accommodations "
        "and I need to take time off work!"
    )
    text = session.turns()[-1]["text"].lower()
    for expected in (
        "find a plus-one",
        "get a nice suit",
        "get the tickets to travel there",
        "accommodations",
        "take time off work",
    ):
        assert expected in text
    assert "define the goal and outcome" not in text


def test_conversational_mode_strips_file_residue_markers(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns, user_message, kw
        return {
            "answer": (
                "1. coffee\n2. rice\n3. apples\n"
                '{"action":"create_file","goal":"save to groceries.md","write_file":{"path":"groceries.md"}}'
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    session.chat("make me a grocery checklist")
    text = session.turns()[-1]["text"].lower()
    assert "create_file" not in text
    assert ".md" not in text
    assert '"action"' not in text
    assert '"goal"' not in text
    assert '"write_file"' not in text


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


# ---------------------------------------------------------------------------
# Deterministic conversational mode lock tests
# ---------------------------------------------------------------------------

_BANNED_TOKENS = ("preview", "draft", "submit", "submitted", "submission", "queue", "queued")


def _assert_clean_conversational(text: str, *, expect_content: str | None = None) -> None:
    """Assert that text has zero preview/draft/submit/queue leakage."""
    lowered = text.lower()
    for token in _BANNED_TOKENS:
        assert token not in lowered, f"Banned token '{token}' leaked: {text!r}"
    assert "governed" not in lowered
    assert "```json" not in lowered
    assert '"goal"' not in text
    assert '"intent"' not in text
    assert '"action"' not in text
    if expect_content is not None:
        assert expect_content.lower() in lowered


def test_deterministic_checklist_10_runs(tmp_path, monkeypatch):
    """Run the SAME checklist input 10 times — MUST be 100% conversational,
    0% preview leakage, every single time."""

    # Each run gets its own session to avoid state bleed
    for run_idx in range(10):
        from tests.vera_session_helpers import make_vera_session

        session = make_vera_session(monkeypatch, tmp_path / f"run{run_idx}")

        # Vary the LLM reply phrasing to simulate nondeterminism
        if run_idx % 3 == 0:
            fake_answer = "Here's your checklist:\n\n- Coffee\n- Rice\n- Apples"
        elif run_idx % 3 == 1:
            fake_answer = (
                "I've prepared a checklist in the preview pane:\n\n1. Coffee\n2. Rice\n3. Apples"
            )
        else:
            fake_answer = (
                "Here you go!\n\n"
                "- [ ] Coffee\n- [ ] Rice\n- [ ] Apples\n\n"
                "I'll submit this to the queue whenever you're ready."
            )

        async def _fake_reply(*, turns, user_message, answer=fake_answer, **kw):
            return {"answer": answer, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("make a checklist. I need coffee, rice, apples")
        assert res.status_code == 200

        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        _assert_clean_conversational(last_turn["text"], expect_content="coffee")
        assert session.preview() is None, f"Preview leaked on run {run_idx}"


def test_grocery_list_conversational(tmp_path, monkeypatch):
    """Grocery list request must be conversational — no preview language."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        return {
            "answer": ("Here's your grocery list:\n\n- Milk\n- Eggs\n- Bread\n- Butter\n- Cheese"),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a grocery list: milk, eggs, bread, butter, cheese")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="milk")
    assert session.preview() is None


def test_wedding_checklist_grouped(tmp_path, monkeypatch):
    """Wedding checklist must come through as grouped/bullet list."""
    session = make_vera_session(monkeypatch, tmp_path)

    wedding_answer = (
        "**Wedding Prep Checklist**\n\n"
        "**Before the Event**\n"
        "- Take time off work\n"
        "- Buy plane tickets\n"
        "- Get in shape\n\n"
        "**At the Event**\n"
        "- Buy a suit\n"
        "- Call mom\n"
        "- Be best man\n\n"
        "**Logistics**\n"
        "- Rent car\n"
        "- Pack bags"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": wedding_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "make a checklist for me: take time off, buy tickets, get in shape, "
        "buy suit, call mom, tell friend, be best man, rent car, pack"
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="take time off")
    # Must have list structure
    assert "- " in last_turn["text"] or "1." in last_turn["text"]
    assert session.preview() is None


def test_explicit_save_intent_creates_preview(tmp_path, monkeypatch):
    """'save a checklist to a note' must create a governed preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        return {
            "answer": ("Grocery Checklist\n\n1. Milk\n2. Eggs\n3. Bread"),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("save a grocery checklist to a note: milk, eggs, bread")
    assert res.status_code == 200

    preview = session.preview()
    assert preview is not None, "Save intent must produce a governed preview"
    assert "write_file" in preview
    assert "milk" in preview["write_file"]["content"].lower()


def test_save_after_checklist_creates_preview(tmp_path, monkeypatch):
    """checklist → 'save that' must produce a governed preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist = "Here's your list:\n\n- Apples\n- Bananas\n- Oranges"

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {"answer": checklist, "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("make me a checklist: apples, bananas, oranges")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "write_file" in preview
    assert "apples" in preview["write_file"]["content"].lower()


def test_multi_turn_planning_stays_conversational(tmp_path, monkeypatch):
    """Multi-turn: checklist request → details → must stay conversational."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! What items do you need on the checklist?",
                "status": "ok:test",
            }
        # Follow-up details — LLM might add preview language
        return {
            "answer": (
                "Here's your checklist:\n\n"
                "1. Coffee\n2. Rice\n3. Apples\n\n"
                "Take a look at the preview to review it."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("I need a checklist")
    res = session.chat("coffee, rice, and apples")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    assert session.preview() is None


def test_send_without_preview_is_truthful(tmp_path, monkeypatch):
    """'send it' with no preview must be truthful — no fake submission claim."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Here's your checklist:\n\n1. Item A\n2. Item B",
                "status": "ok:test",
            }
        return {
            "answer": "Done! I've submitted it to the queue.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("make me a checklist for work")
    res = session.chat("send it")
    assert res.status_code == 200

    # No preview exists, so no real submission happened
    assert session.preview() is None


def test_hard_sanitizer_strips_novel_preview_phrasing(tmp_path, monkeypatch):
    """Even novel LLM phrasings mentioning 'preview' or 'draft' must be
    stripped by the hard mode lock — not just known phrases."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Use a phrasing that is NOT in _FALSE_CLAIM_PHRASES but contains 'preview'
    answer_with_novel_phrasing = (
        "Here's your checklist:\n\n"
        "1. Coffee\n"
        "2. Rice\n"
        "3. Apples\n\n"
        "You can see everything in the interactive preview widget on the right."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer_with_novel_phrasing, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist: coffee, rice, apples")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    assert session.preview() is None


def test_hard_sanitizer_preserves_list_items_with_draft_word(tmp_path, monkeypatch):
    """List items that legitimately contain 'draft' (e.g. 'Draft the proposal')
    must be preserved — only non-list-item lines are stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your checklist:\n\n"
        "1. Draft the proposal\n"
        "2. Submit the application\n"
        "3. Review the draft document\n\n"
        "I've loaded this into the preview pane for you."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the grant application")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # List items preserved even though they contain 'draft'/'submit'
    assert "draft the proposal" in last_turn["text"].lower()
    assert "submit the application" in last_turn["text"].lower()
    assert "review the draft document" in last_turn["text"].lower()
    # Non-list-item preview claim stripped
    assert "preview pane" not in last_turn["text"].lower()
    assert session.preview() is None


def test_execution_mode_classification_is_deterministic(tmp_path, monkeypatch):
    """_classify_execution_mode must return the same result for the same input."""
    from voxera.vera_web.app import ExecutionMode, _classify_execution_mode

    conversational_inputs = [
        "make a checklist. I need coffee, rice, apples",
        "help me plan a vacation to Japan",
        "brainstorm ideas for the party",
        "make me a grocery list: milk, eggs, bread",
        "what do I need to prepare for the interview",
        "create a packing list for the trip",
        "organize my tasks for the week",
        "to do for today",
    ]
    for msg in conversational_inputs:
        for _ in range(10):
            mode = _classify_execution_mode(msg, prior_planning_active=False, pending_preview=None)
            assert mode is ExecutionMode.CONVERSATIONAL_ARTIFACT, (
                f"Expected CONVERSATIONAL_ARTIFACT for {msg!r}, got {mode}"
            )

    governed_inputs = [
        "save a checklist to a note for my wedding prep",
        "write a checklist to a file called list.txt",
        "save that to a note",
        "export my list as a markdown file",
    ]
    for msg in governed_inputs:
        for _ in range(10):
            mode = _classify_execution_mode(msg, prior_planning_active=False, pending_preview=None)
            assert mode is ExecutionMode.GOVERNED_PREVIEW, (
                f"Expected GOVERNED_PREVIEW for {msg!r}, got {mode}"
            )


# ---------------------------------------------------------------------------
# Deterministic plain-text artifact output — no workflow narration leakage
# ---------------------------------------------------------------------------


def test_checklist_with_save_when_ready_language_stripped(tmp_path, monkeypatch):
    """'When you're ready' / 'I can save this' workflow language must be
    stripped from conversational checklist output."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your checklist:\n\n"
        "1. Coffee\n"
        "2. Rice\n"
        "3. Apples\n\n"
        "Let me know when you're ready and I can save this for you."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make a checklist. I need coffee, rice, apples")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    assert "when you're ready" not in last_turn["text"].lower()
    assert "i can save" not in last_turn["text"].lower()
    assert "let me know" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_does_this_look_right_stripped(tmp_path, monkeypatch):
    """'Does this look right?' meta-question must be stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your wedding checklist:\n\n"
        "- Take time off\n"
        "- Buy plane tickets\n"
        "- Get a suit\n\n"
        "Does this look right before we save it?"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a wedding checklist: time off, tickets, suit")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="take time off")
    assert "does this look right" not in last_turn["text"].lower()
    assert "before we save" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_take_a_look_stripped(tmp_path, monkeypatch):
    """'Take a look' workflow prompt must be stripped even without 'preview'."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's what I put together:\n\n"
        "1. Milk\n"
        "2. Eggs\n"
        "3. Bread\n\n"
        "Take a look and let me know if you'd like to adjust anything."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a grocery list: milk, eggs, bread")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="milk")
    assert "take a look" not in last_turn["text"].lower()
    assert "let me know" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_meta_commentary_stripped(tmp_path, monkeypatch):
    """Pure meta-commentary like 'I've organized the tasks logically' must be
    stripped when actual list items are present."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "I've organized the tasks logically for your trip preparation.\n\n"
        "1. Book flights\n"
        "2. Reserve hotel\n"
        "3. Plan activities\n"
        "4. Pack bags\n\n"
        "I've grouped the items by priority for you."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan for a vacation to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="book flights")
    assert "i've organized" not in last_turn["text"].lower()
    assert "i've grouped" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_whenever_ready_and_shall_i_save_stripped(tmp_path, monkeypatch):
    """Multiple workflow phrases in one response must all be stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your packing list:\n\n"
        "- Passport\n"
        "- Clothes\n"
        "- Charger\n"
        "- Toiletries\n\n"
        "Whenever you're ready, shall I save this to a note?"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a packing list: passport, clothes, charger, toiletries")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="passport")
    assert "whenever you're ready" not in last_turn["text"].lower()
    assert "shall i save" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_without_workflow_narration_passes_through(tmp_path, monkeypatch):
    """A clean checklist with no workflow language must pass through unmodified."""
    session = make_vera_session(monkeypatch, tmp_path)

    clean_answer = (
        "**Wedding Prep**\n\n"
        "1. Take time off work\n"
        "2. Buy plane tickets\n"
        "3. Get a nice suit\n"
        "4. Call mom"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": clean_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a wedding checklist")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["text"] == clean_answer


def test_meta_commentary_only_renders_deterministic_checklist(tmp_path, monkeypatch):
    """If the LLM returns only narration, conversational planning still must
    render actual checklist items deterministically."""
    session = make_vera_session(monkeypatch, tmp_path)

    narration_only = (
        "I've organized your trip tasks. You'll want to focus on booking "
        "flights first, then move on to accommodations and activities."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": narration_only, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan a trip to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert re.search(r"(?m)^- ", last_turn["text"])
    assert "preview" not in last_turn["text"].lower()
    assert "draft" not in last_turn["text"].lower()


def test_two_turn_checklist_produces_actual_content(tmp_path, monkeypatch):
    """Two-turn flow must produce actual checklist items, not just narration."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! What items should go on the checklist?",
                "status": "ok:test",
            }
        return {
            "answer": (
                "I've compiled your list:\n\n"
                "1. Coffee\n"
                "2. Rice\n"
                "3. Apples\n\n"
                "Would you like me to save this when you're ready?"
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("I need a checklist")
    res = session.chat("coffee, rice, apples")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    # Meta-commentary stripped since list items present
    assert "i've compiled" not in last_turn["text"].lower()
    assert "would you like me to save" not in last_turn["text"].lower()
    assert "when you're ready" not in last_turn["text"].lower()
    assert session.preview() is None


def test_deterministic_one_turn_10_runs_no_workflow(tmp_path, monkeypatch):
    """10 runs of the same one-turn checklist — MUST be 100% consistent,
    zero workflow/save/preview narration, actual content every time."""

    for run_idx in range(10):
        from tests.vera_session_helpers import make_vera_session as _make

        session = _make(monkeypatch, tmp_path / f"wf_run{run_idx}")

        # Rotate through problematic LLM phrasings
        variant = run_idx % 5
        if variant == 0:
            fake_answer = "Here's your checklist:\n\n- Coffee\n- Rice\n- Apples"
        elif variant == 1:
            fake_answer = (
                "I've organized everything for you:\n\n"
                "1. Coffee\n2. Rice\n3. Apples\n\n"
                "Take a look and let me know when you're ready to save."
            )
        elif variant == 2:
            fake_answer = (
                "Here you go!\n\n"
                "- [ ] Coffee\n- [ ] Rice\n- [ ] Apples\n\n"
                "Does this look right? I can save this for you whenever you're ready."
            )
        elif variant == 3:
            fake_answer = (
                "I've compiled your grocery list:\n\n"
                "1. Coffee\n2. Rice\n3. Apples\n\n"
                "Shall I save this to a note?"
            )
        else:
            fake_answer = (
                "- Coffee\n- Rice\n- Apples\n\n"
                "I've grouped these items. Let me know if you'd like to adjust "
                "or save this list."
            )

        async def _fake_reply(*, turns, user_message, answer=fake_answer, **kw):
            return {"answer": answer, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("make a checklist. I need coffee, rice, apples")
        assert res.status_code == 200

        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        text = last_turn["text"]
        lowered = text.lower()

        # Must contain actual checklist content
        assert "coffee" in lowered, f"Missing content on run {run_idx}"

        # Must NOT contain any workflow narration
        for banned in (
            "preview",
            "draft",
            "submit",
            "queue",
            "when you're ready",
            "whenever you're ready",
            "shall i save",
            "i can save",
            "want me to save",
            "does this look right",
            "does this look good",
            "take a look",
            "let me know",
            "before we save",
            "before saving",
        ):
            assert banned not in lowered, (
                f"Workflow narration '{banned}' leaked on run {run_idx}: {text!r}"
            )

        assert session.preview() is None, f"Preview leaked on run {run_idx}"


# ---------------------------------------------------------------------------
# Final rendering determinism — JSON, bare payloads, meta-commentary coverage
# ---------------------------------------------------------------------------


def test_checklist_with_unfenced_json_payload_stripped(tmp_path, monkeypatch):
    """Unfenced JSON payload (no ``` fencing) must be stripped from
    conversational checklist output."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your checklist:\n\n"
        "1. Coffee\n"
        "2. Rice\n"
        "3. Apples\n\n"
        '{"intent": "create_checklist", "items": ["coffee", "rice", "apples"]}'
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make a checklist. I need coffee, rice, apples")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    assert '"intent"' not in last_turn["text"]
    assert session.preview() is None


def test_checklist_with_bare_goal_json_stripped(tmp_path, monkeypatch):
    """Bare JSON with 'goal' key must be stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "- Passport\n"
        "- Clothes\n"
        "- Charger\n\n"
        '{"goal": "create packing list", "write_file": {"path": "~/notes/list.md"}}'
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a packing list: passport, clothes, charger")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="passport")
    assert '"goal"' not in last_turn["text"]
    assert '"write_file"' not in last_turn["text"]
    assert session.preview() is None


def test_checklist_with_multiline_unfenced_json_stripped(tmp_path, monkeypatch):
    """Multi-line unfenced JSON block must be stripped."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's your list:\n\n"
        "1. Milk\n"
        "2. Eggs\n\n"
        "{\n"
        '  "intent": "create_checklist",\n'
        '  "items": ["milk", "eggs"]\n'
        "}"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a grocery list: milk, eggs")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="milk")
    assert '"intent"' not in last_turn["text"]
    assert session.preview() is None


def test_broader_meta_commentary_stripped(tmp_path, monkeypatch):
    """Broader meta-commentary phrasings must be stripped when list items
    are present."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "Here's what I came up with:\n\n"
        "1. Book flights\n"
        "2. Reserve hotel\n"
        "3. Plan activities\n\n"
        "I've broken it down into the key steps for your trip."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan a vacation to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="book flights")
    assert "here's what i came up with" not in last_turn["text"].lower()
    assert "i've broken it down" not in last_turn["text"].lower()
    assert session.preview() is None


def test_deterministic_final_render_10_runs(tmp_path, monkeypatch):
    """10 runs with maximally adversarial LLM outputs — must ALWAYS produce
    clean checklist content with no JSON, no meta-only, no workflow."""

    for run_idx in range(10):
        from tests.vera_session_helpers import make_vera_session as _make

        session = _make(monkeypatch, tmp_path / f"final_run{run_idx}")

        # Each variant tests a different failure mode
        variant = run_idx % 5
        if variant == 0:
            # Clean output — should pass through
            fake_answer = "- Coffee\n- Rice\n- Apples"
        elif variant == 1:
            # Unfenced JSON payload
            fake_answer = (
                "1. Coffee\n2. Rice\n3. Apples\n\n"
                '{"intent": "create_checklist", "items": ["coffee"]}'
            )
        elif variant == 2:
            # Meta-commentary + workflow narration
            fake_answer = (
                "I've organized everything logically for you.\n\n"
                "- Coffee\n- Rice\n- Apples\n\n"
                "Does this look right? Let me know when you're ready."
            )
        elif variant == 3:
            # Multi-line unfenced JSON
            fake_answer = (
                "- Coffee\n- Rice\n- Apples\n\n"
                "{\n"
                '  "goal": "grocery list",\n'
                '  "write_file": {"path": "~/notes/list.md"}\n'
                "}"
            )
        else:
            # All leakage types combined
            fake_answer = (
                "I've put together your list:\n\n"
                "1. Coffee\n2. Rice\n3. Apples\n\n"
                "Take a look at the preview pane to review.\n"
                "I can save this whenever you're ready.\n"
                '{"intent": "save", "items": ["coffee"]}'
            )

        async def _fake_reply(*, turns, user_message, answer=fake_answer, **kw):
            return {"answer": answer, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("make a checklist. I need coffee, rice, apples")
        assert res.status_code == 200

        last_turn = session.turns()[-1]
        text = last_turn["text"]
        lowered = text.lower()

        # Must contain actual checklist content
        assert "coffee" in lowered, f"Missing content on run {run_idx}: {text!r}"

        # Must NOT contain any forbidden output
        for banned in (
            '"intent"',
            '"goal"',
            '"action"',
            '"write_file"',
        ):
            assert banned not in text, f"JSON key {banned} leaked on run {run_idx}: {text!r}"
        for banned_phrase in (
            "preview",
            "draft",
            "submit",
            "queue",
            "when you're ready",
            "let me know",
            "take a look",
            "does this look right",
        ):
            assert banned_phrase not in lowered, (
                f"Forbidden phrase '{banned_phrase}' leaked on run {run_idx}: {text!r}"
            )

        assert session.preview() is None, f"Preview leaked on run {run_idx}"


# ---------------------------------------------------------------------------
# Hard-lock deterministic rendering — empty fallback and enforcement layer
# ---------------------------------------------------------------------------


def test_sanitizer_empty_fallback_does_not_restore_banned_content(tmp_path, monkeypatch):
    """When the LLM produces ONLY preview/workflow language with no list items,
    the sanitizer must NOT fall back to the original banned text."""
    session = make_vera_session(monkeypatch, tmp_path)

    # All banned content, zero list items
    pure_leakage_answer = (
        "I've drafted a checklist for you.\n"
        "You can review the preview pane to see everything.\n"
        "Once you're happy with the content, I can save this as a file.\n"
        "I'll submit it to the queue whenever you're ready."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": pure_leakage_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"])
    # Must NOT have fallen back to the original text
    assert "preview pane" not in last_turn["text"].lower()
    assert "save this as a file" not in last_turn["text"].lower()
    assert session.preview() is None


def test_sanitizer_empty_fallback_extracts_items_from_mixed_output(tmp_path, monkeypatch):
    """When the sanitizer empties non-list lines, items must survive.
    When ALL non-list content is banned, items should be re-extracted."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Items exist but surrounded by banned content only
    answer = (
        "I've prepared this in the preview pane for you:\n\n"
        "1. Take time off\n"
        "2. Buy the tickets\n"
        "3. Get in shape\n\n"
        "I've submitted the checklist to the queue for processing."
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "yes can you make a checklist of the following: take time off, "
        "buy the tickets, get in shape"
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="take time off")
    assert "buy the tickets" in last_turn["text"].lower()
    assert "get in shape" in last_turn["text"].lower()
    assert session.preview() is None


def test_grocery_checklist_with_preview_language_deterministic(tmp_path, monkeypatch):
    """Grocery checklist with preview language — matches Failure B from issue."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer = (
        "I've prepared a grocery checklist for you in the preview.\n\n"
        "- Coffee\n"
        "- Rice\n"
        "- Apples and bananas\n"
        "- Bread\n"
        "- Cheese\n"
        "- Ham\n"
        "- Pasta"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        return {"answer": answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "make a checklist. I need coffee, rice, apples and bannanas, "
        "bread, chess, ham and some pasta."
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="coffee")
    assert "rice" in last_turn["text"].lower()
    assert "pasta" in last_turn["text"].lower()
    assert "in the preview" not in last_turn["text"].lower()
    assert session.preview() is None


def test_two_turn_planning_with_json_payload_stripped(tmp_path, monkeypatch):
    """Two-turn flow where follow-up produces JSON — matches Failure C."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "checklist" in user_message.lower():
            return {
                "answer": "Sure! Tell me what you need to get done.",
                "status": "ok:test",
            }
        # Follow-up: LLM emits JSON payload
        return {
            "answer": (
                "Here's your checklist:\n\n"
                "1. Find a plus-one\n"
                "2. Book travel\n"
                "3. Get a suit\n\n"
                '```json\n{"intent": "create_checklist", "items": '
                '["find plus-one", "book travel", "get suit"]}\n```\n'
                "I've prepared this in the preview for you."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("create a checklist would surely help on the many things I need to do.")
    res = session.chat("First I need to find a +1, book travel, and get a suit")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    _assert_clean_conversational(last_turn["text"], expect_content="find a plus-one")
    assert "```json" not in last_turn["text"]
    assert '"intent"' not in last_turn["text"]
    assert "in the preview" not in last_turn["text"].lower()
    assert session.preview() is None


def test_wedding_checklist_repeated_5_runs_deterministic(tmp_path, monkeypatch):
    """Exact wedding checklist prompt from issue — 5 runs, must be 100% clean."""

    for run_idx in range(5):
        from tests.vera_session_helpers import make_vera_session as _make

        session = _make(monkeypatch, tmp_path / f"wedding_run{run_idx}")

        # Rotate through observed bad output patterns from the issue
        variant = run_idx % 3
        if variant == 0:
            fake_answer = (
                "I've drafted a checklist for you. Review the preview pane.\n\n"
                "- Take time off\n- Buy the tickets\n- Get in shape\n"
                "- Buy the suit\n- Call mom\n- Tell my friend\n"
                "- Be the best man\n- Rent a car\n- Pack"
            )
        elif variant == 1:
            fake_answer = (
                "Here's your checklist:\n\n"
                "1. Take time off\n2. Buy the tickets\n3. Get in shape\n"
                "4. Buy the suit\n5. Call mom\n6. Tell my friend\n"
                "7. Be the best man\n8. Rent a car\n9. Pack\n\n"
                "Once you're happy with the content, I can save this as a file."
            )
        else:
            fake_answer = (
                "- Take time off\n- Buy the tickets\n- Get in shape\n"
                "- Buy the suit\n- Call mom\n- Tell my friend\n"
                "- Be the best man\n- Rent a car\n- Pack"
            )

        async def _fake_reply(*, turns, user_message, answer=fake_answer, **kw):
            return {"answer": answer, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat(
            "yes can you make a checklist of the following: take time off, "
            "buy the tickets, get in shape, buy the suit, call mom, "
            "tell my friend, be the best man at a wedding, rent a car "
            "during the trip, pack."
        )
        assert res.status_code == 200

        last_turn = session.turns()[-1]
        _assert_clean_conversational(last_turn["text"], expect_content="take time off")
        assert "buy the tickets" in last_turn["text"].lower()
        assert "pack" in last_turn["text"].lower()
        assert session.preview() is None, f"Preview leaked on wedding run {run_idx}"


def test_enforcement_layer_catches_sanitizer_edge_case(tmp_path, monkeypatch):
    """If the sanitizer somehow misses a violation, the enforcement layer
    must catch it and re-render deterministically."""
    from voxera.vera_web.conversational_checklist import (
        enforce_conversational_checklist_output as _enforce_conversational_checklist_output,
    )

    # Simulate a sanitizer output that still has a banned token in a non-list line
    dirty = "1. Coffee\n2. Rice\nCheck the preview for more."
    clean = _enforce_conversational_checklist_output(
        dirty, raw_answer=dirty, user_message="make a grocery checklist"
    )
    assert "preview" not in clean.lower()
    assert "coffee" in clean.lower()
    assert "rice" in clean.lower()


def test_enforcement_layer_handles_empty_text(tmp_path, monkeypatch):
    """Enforcement layer must extract items from raw_answer when text is empty."""
    from voxera.vera_web.conversational_checklist import (
        enforce_conversational_checklist_output as _enforce_conversational_checklist_output,
    )

    raw = "I prepared this in the preview.\n\n- Milk\n- Eggs\n- Bread"
    result = _enforce_conversational_checklist_output(
        "", raw_answer=raw, user_message="make a grocery checklist"
    )
    assert "milk" in result.lower()
    assert "eggs" in result.lower()
    assert "preview" not in result.lower()
