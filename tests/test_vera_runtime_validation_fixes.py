"""Regression tests for the three runtime validation bugs:

1. Summary preview-body purity (helper/control text contamination)
2. Submit-intent strictness (typo-like near-submit phrases)
3. Post-preview rename mutation correctness
4. Preservation of current passing joke/fact/poem flows
"""

from __future__ import annotations

import json

from voxera.core.writing_draft_intent import extract_text_draft_from_reply
from voxera.vera.draft_revision import (
    _detect_content_type_from_preview,
    _generate_refreshed_content,
    _is_ambiguous_change_request,
    _is_clear_content_refresh_request,
    interpret_active_preview_draft_revision,
)
from voxera.vera.preview_submission import (
    is_near_miss_submit_phrase,
    is_preview_submission_request,
    should_submit_active_preview,
)
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# Bug 1 — Summary preview-body purity
# ---------------------------------------------------------------------------


class TestSummaryPreviewBodyPurity:
    """Preview content must not contain helper/control narration."""

    def test_strip_you_can_review_prefix(self):
        text = (
            "You can review the content and authorize the file creation in the preview pane.\n\n"
            "Mauna Loa is the world's largest active volcano, located on the Big Island of Hawai'i. "
            "It rises approximately 4,169 meters above sea level."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "you can review" not in result.lower()
        assert "authorize" not in result.lower()
        assert "preview pane" not in result.lower()
        assert "mauna loa" in result.lower()

    def test_strip_please_review_prefix(self):
        text = (
            "Please review the content below.\n\nMauna Loa is the world's largest active volcano."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "please review" not in result.lower()
        assert "mauna loa" in result.lower()

    def test_strip_trailing_review_wrapper(self):
        text = (
            "Mauna Loa is the world's largest active volcano on Hawai'i.\n\n"
            "You can review the content in the preview pane and submit when ready."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "you can review" not in result.lower()
        assert "submit when ready" not in result.lower()
        assert "mauna loa" in result.lower()

    def test_summary_generate_save_produces_pure_body(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            if "summary" in user_message.lower() and "mauna loa" in user_message.lower():
                return {
                    "answer": (
                        "You can review the content and authorize the file creation "
                        "in the preview pane.\n\n"
                        "Mauna Loa is the world's largest active volcano, located on the "
                        "Big Island of Hawai'i. It rises approximately 4,169 meters above "
                        "sea level and last erupted in 2022."
                    ),
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("give me a short summary of Mauna Loa and save it as maunaloa.txt")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/maunaloa.txt"
        content = preview["write_file"]["content"]
        assert "mauna loa" in content.lower()
        assert "you can review" not in content.lower()
        assert "please review" not in content.lower()
        assert "authorize" not in content.lower()
        assert "preview pane" not in content.lower()

    def test_summary_with_please_review_prefix_produces_pure_body(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            if "summary" in user_message.lower():
                return {
                    "answer": (
                        "Please review the content below.\n\n"
                        "Mount Everest is the tallest mountain on Earth, standing at 8,849 meters."
                    ),
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("give me a short summary of Everest and save it as everest.txt")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None
        content = preview["write_file"]["content"]
        assert "mount everest" in content.lower()
        assert "please review" not in content.lower()


# ---------------------------------------------------------------------------
# Bug 2 — Submit-intent strictness / typo-like submit phrasing
# ---------------------------------------------------------------------------


class TestSubmitIntentStrictness:
    """Typo-like near-submit phrases must fail closed or route canonically."""

    def test_send_iit_is_near_miss(self):
        assert is_near_miss_submit_phrase("send iit")

    def test_send_it_is_not_near_miss(self):
        assert not is_near_miss_submit_phrase("send it")

    def test_submit_it_is_not_near_miss(self):
        assert not is_near_miss_submit_phrase("submit it")

    def test_sned_it_is_near_miss(self):
        assert is_near_miss_submit_phrase("sned it")

    def test_sendit_is_near_miss(self):
        assert is_near_miss_submit_phrase("sendit")

    def test_send_iiit_is_near_miss(self):
        assert is_near_miss_submit_phrase("send iiit")

    def test_normal_text_is_not_near_miss(self):
        assert not is_near_miss_submit_phrase("hello there")

    def test_near_miss_does_not_trigger_canonical_submit(self):
        assert not should_submit_active_preview("send iit", preview_available=True)
        assert not is_preview_submission_request("send iit")

    def test_near_miss_submit_fails_closed_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {"answer": "Here is a poem about rain.", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        # Create a preview first
        session.chat("write a short poem and save it as poem.txt")
        preview = session.preview()
        assert preview is not None

        # Typo-like submit should fail closed
        res = session.chat("send iit")
        assert res.status_code == 200

        last_turn = session.turns()[-1]["text"].lower()
        assert "did not submit" in last_turn
        # Preview should still exist (not submitted)
        assert session.preview() is not None
        # No inbox files (nothing was queued)
        assert list((session.queue / "inbox").glob("*.json")) == []

    def test_near_miss_submit_without_preview_also_fails_closed(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("send iit")
        assert res.status_code == 200
        last_turn = session.turns()[-1]["text"].lower()
        assert "did not submit" in last_turn
        assert list((session.queue / "inbox").glob("*.json")) == []

    def test_real_submit_still_works_after_near_miss(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {"answer": "Here is your poem about clouds.", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        assert session.preview() is not None

        # Near-miss fails closed
        session.chat("send iit")
        assert session.preview() is not None

        # Real submit succeeds
        submit = session.chat("send it")
        assert submit.status_code == 200
        inbox_files = list((session.queue / "inbox").glob("*.json"))
        assert len(inbox_files) == 1
        assert session.preview() is None


# ---------------------------------------------------------------------------
# Bug 3 — Post-preview rename mutation correctness
# ---------------------------------------------------------------------------


class TestPostPreviewRenameMutation:
    """Clear rename instructions after preview must update the canonical path."""

    def test_call_it_renames_preview_deterministic(self):
        preview = {
            "goal": "write a file called note-1234567.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note-1234567.txt",
                "content": "Mauna Loa is the largest active volcano.",
                "mode": "overwrite",
            },
        }
        revision = interpret_active_preview_draft_revision("call it volcano.txt", preview)
        assert revision is not None
        assert revision["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"
        assert revision["write_file"]["content"] == "Mauna Loa is the largest active volcano."

    def test_name_it_renames_preview_deterministic(self):
        preview = {
            "goal": "write a file called note-1234567.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note-1234567.txt",
                "content": "Volcano facts here.",
                "mode": "overwrite",
            },
        }
        revision = interpret_active_preview_draft_revision("name it volcano.txt", preview)
        assert revision is not None
        assert revision["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"
        assert revision["write_file"]["content"] == "Volcano facts here."

    def test_rename_it_renames_preview_deterministic(self):
        preview = {
            "goal": "write a file called note-1234567.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note-1234567.txt",
                "content": "Earth's biggest volcano.",
                "mode": "overwrite",
            },
        }
        revision = interpret_active_preview_draft_revision("rename it to volcano.txt", preview)
        assert revision is not None
        assert revision["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"
        assert revision["write_file"]["content"] == "Earth's biggest volcano."

    def test_rename_preserves_content_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            if "biggest volcano" in user_message.lower():
                return {
                    "answer": (
                        "Mauna Loa is the world's largest active volcano, located on the "
                        "Big Island of Hawai'i."
                    ),
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me about the biggest volcano on earth")
        session.chat("save it to a note")
        preview_before = session.preview()
        assert preview_before is not None
        assert preview_before["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")
        original_content = preview_before["write_file"]["content"]
        assert "mauna loa" in original_content.lower()

        session.chat("call it volcano.txt")
        preview_after = session.preview()
        assert preview_after is not None
        assert preview_after["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"
        assert preview_after["write_file"]["content"] == original_content

    def test_name_it_variant_renames_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            if "biggest volcano" in user_message.lower():
                return {
                    "answer": "Mauna Loa is the world's largest active volcano.",
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me about the biggest volcano on earth")
        session.chat("save it to a note")
        session.chat("name it volcano.txt")

        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"

    def test_rename_it_to_variant_renames_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            if "biggest volcano" in user_message.lower():
                return {
                    "answer": "Mauna Loa is the world's largest active volcano.",
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me about the biggest volcano on earth")
        session.chat("save it to a note")
        session.chat("rename it to volcano.txt")

        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"

    def test_rename_does_not_leave_stale_auto_filename(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {"answer": "Some informational content about volcanoes.", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me about volcanoes")
        session.chat("save it to a note")
        auto_preview = session.preview()
        assert auto_preview is not None
        auto_path = auto_preview["write_file"]["path"]
        assert "note-" in auto_path

        session.chat("call it volcano.txt")
        renamed_preview = session.preview()
        assert renamed_preview is not None
        assert renamed_preview["write_file"]["path"] == "~/VoxeraOS/notes/volcano.txt"
        assert renamed_preview["write_file"]["path"] != auto_path


# ---------------------------------------------------------------------------
# Preservation coverage — currently passing flows
# ---------------------------------------------------------------------------


class TestPreservationOfPassingFlows:
    """Characterization tests confirming joke/fact/poem flows are not regressed."""

    def test_poem_flow_preview_path_correct(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "Roses are red, violets are blue, this is a poem, written for you.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
        assert "roses are red" in preview["write_file"]["content"].lower()

    def test_joke_flow_clean_preview_body(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "Why don't astronauts ever get hungry in space? Because they just had a big launch.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me an astronaut joke and save it as astrojoke.txt")
        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/astrojoke.txt"
        content = preview["write_file"]["content"]
        assert "big launch" in content.lower()
        assert "you can review" not in content.lower()
        assert "please review" not in content.lower()

    def test_fact_flow_clean_preview_body(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "Mauna Loa is the largest active volcano on Earth, covering over half of the Big Island.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("give me a short volcano fact and save it as volcanofact.txt")
        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/volcanofact.txt"
        content = preview["write_file"]["content"]
        assert "mauna loa" in content.lower()
        assert "you can review" not in content.lower()
        assert "please review" not in content.lower()

    def test_submit_after_preview_handoff_produces_queue_job(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "Here is a lovely poem about stars shining bright.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        assert session.preview() is not None

        submit = session.chat("submit it")
        assert submit.status_code == 200
        inbox_files = list((session.queue / "inbox").glob("*.json"))
        assert len(inbox_files) == 1
        payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
        assert payload["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
        assert session.preview() is None


# ---------------------------------------------------------------------------
# Active-draft content refresh
# ---------------------------------------------------------------------------


class TestActiveDraftContentRefresh:
    """Clear content-refresh requests on an active preview must replace the body."""

    # ── Unit-level: helper function tests ──

    def test_clear_refresh_detected_for_generate_different_poem(self):
        assert _is_clear_content_refresh_request("generate a different poem")

    def test_clear_refresh_detected_for_tell_me_different_joke(self):
        assert _is_clear_content_refresh_request("tell me a different joke")

    def test_clear_refresh_detected_for_give_me_shorter_summary(self):
        assert _is_clear_content_refresh_request("give me a shorter summary")

    def test_clear_refresh_detected_for_give_me_different_fact(self):
        assert _is_clear_content_refresh_request("give me a different fact")

    def test_clear_refresh_detected_for_change_the_poem(self):
        assert _is_clear_content_refresh_request("change the poem")

    def test_clear_refresh_not_detected_for_change_it(self):
        assert not _is_clear_content_refresh_request("change it")

    def test_clear_refresh_not_detected_for_make_it_better(self):
        assert not _is_clear_content_refresh_request("make it better")

    def test_generate_refreshed_poem_produces_content(self):
        result = _generate_refreshed_content("poem", "Roses are red.")
        assert result is not None
        assert result.strip()
        assert result != "Roses are red."

    def test_generate_refreshed_joke_produces_content(self):
        result = _generate_refreshed_content("joke", "Old joke.")
        assert result is not None
        assert result.strip()

    def test_generate_refreshed_fact_produces_content(self):
        result = _generate_refreshed_content("fact", "Old fact.")
        assert result is not None
        assert result.strip()

    def test_generate_refreshed_summary_compresses(self):
        original = (
            "Mauna Loa is the world's largest active volcano. "
            "It is located on the Big Island. "
            "It rises 4,169 meters."
        )
        result = _generate_refreshed_content("summary", original)
        assert result is not None
        assert len(result) < len(original)

    def test_detect_content_type_poem(self):
        preview = {
            "write_file": {"path": "~/VoxeraOS/notes/poem.txt"},
            "goal": "write a file",
        }
        assert _detect_content_type_from_preview(preview, "generate a different poem") == "poem"

    def test_detect_content_type_joke(self):
        preview = {
            "write_file": {"path": "~/VoxeraOS/notes/joke.txt"},
            "goal": "write a file",
        }
        assert _detect_content_type_from_preview(preview, "tell me a different joke") == "joke"

    def test_refreshed_poem_has_no_helper_text(self):
        content = _generate_refreshed_content("poem", "Old poem text.")
        assert content is not None
        assert "updated the draft" not in content.lower()
        assert "you can review" not in content.lower()
        assert "if that looks good" not in content.lower()
        assert "preview" not in content.lower()

    def test_refreshed_joke_has_no_helper_text(self):
        content = _generate_refreshed_content("joke", "Old joke.")
        assert content is not None
        assert "updated the draft" not in content.lower()
        assert "you can review" not in content.lower()

    # ── Ambiguity: fail closed ──

    def test_change_it_fails_closed(self):
        preview = {
            "goal": "write a file called poem.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/poem.txt",
                "content": "A poem about rain.",
                "mode": "overwrite",
            },
        }
        result = interpret_active_preview_draft_revision("change it", preview)
        assert result is None

    def test_make_it_better_fails_closed(self):
        preview = {
            "goal": "write a file called poem.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/poem.txt",
                "content": "A poem about rain.",
                "mode": "overwrite",
            },
        }
        result = interpret_active_preview_draft_revision("make it better", preview)
        assert result is None

    def test_fix_it_fails_closed(self):
        preview = {
            "goal": "write a file called joke.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/joke.txt",
                "content": "A joke.",
                "mode": "overwrite",
            },
        }
        result = interpret_active_preview_draft_revision("fix it", preview)
        assert result is None

    def test_ambiguous_change_it_detected(self):
        assert _is_ambiguous_change_request("change it")

    def test_ambiguous_make_it_better_detected(self):
        assert _is_ambiguous_change_request("make it better")

    def test_ambiguous_fix_it_detected(self):
        assert _is_ambiguous_change_request("fix it")

    def test_specific_type_not_ambiguous(self):
        assert not _is_ambiguous_change_request("change the poem")

    # ── Session-level: content refresh in full Vera flow ──

    def test_poem_refresh_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        call_count = [0]

        async def _fake_reply(*, turns, user_message):
            _ = turns
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "answer": "Roses are red, violets are blue, this poem is for you.",
                    "status": "ok:test",
                }
            return {
                "answer": "Stars above the quiet sea, flickering lights of mystery.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        preview_before = session.preview()
        assert preview_before is not None
        assert preview_before["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
        original_content = preview_before["write_file"]["content"]

        session.chat("generate a different poem")
        preview_after = session.preview()
        assert preview_after is not None
        assert preview_after["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
        assert preview_after["write_file"]["content"] != original_content
        assert preview_after["write_file"]["content"].strip()

    def test_joke_refresh_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        call_count = [0]

        async def _fake_reply(*, turns, user_message):
            _ = turns
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "answer": "Why did the chicken cross the road? To get to the other side.",
                    "status": "ok:test",
                }
            return {
                "answer": "I told my computer I needed a break. It said 'No problem, I'll go to sleep.'",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("tell me a dad joke and save it as dadjoke.txt")
        preview_before = session.preview()
        assert preview_before is not None
        original_content = preview_before["write_file"]["content"]

        session.chat("tell me a different joke and add it as content")
        preview_after = session.preview()
        assert preview_after is not None
        assert preview_after["write_file"]["path"] == "~/VoxeraOS/notes/dadjoke.txt"
        assert preview_after["write_file"]["content"] != original_content

    def test_fact_refresh_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        call_count = [0]

        async def _fake_reply(*, turns, user_message):
            _ = turns
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "answer": "Mauna Loa is the largest active volcano, covering over half of the Big Island.",
                    "status": "ok:test",
                }
            return {
                "answer": "Octopuses have three hearts and blue blood.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("give me a short volcano fact and save it as volcanofact.txt")
        preview_before = session.preview()
        assert preview_before is not None

        session.chat("give me a different fact")
        preview_after = session.preview()
        assert preview_after is not None
        assert preview_after["write_file"]["path"] == "~/VoxeraOS/notes/volcanofact.txt"
        assert preview_after["write_file"]["content"] != preview_before["write_file"]["content"]

    def test_ambiguous_request_fails_closed_in_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "A lovely poem about the sea.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        preview_before = session.preview()
        assert preview_before is not None
        original_content = preview_before["write_file"]["content"]

        session.chat("change it")
        preview_after = session.preview()
        assert preview_after is not None
        # Content must be unchanged
        assert preview_after["write_file"]["content"] == original_content
        # Response should indicate fail-closed
        last_turn = session.turns()[-1]["text"].lower()
        assert "unchanged" in last_turn or "ambiguous" in last_turn

    # ── Submit after refresh ──

    def test_submit_after_refresh_uses_refreshed_body(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message):
            _ = turns
            return {
                "answer": "Old poem about the stars above.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write a short poem and save it as poem.txt")
        assert session.preview() is not None

        session.chat("generate a different poem")
        refreshed_preview = session.preview()
        assert refreshed_preview is not None
        refreshed_content = refreshed_preview["write_file"]["content"]

        submit = session.chat("send it")
        assert submit.status_code == 200
        inbox_files = list((session.queue / "inbox").glob("*.json"))
        assert len(inbox_files) == 1
        payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
        assert payload["write_file"]["path"] == "~/VoxeraOS/notes/poem.txt"
        assert payload["write_file"]["content"] == refreshed_content
        assert session.preview() is None
