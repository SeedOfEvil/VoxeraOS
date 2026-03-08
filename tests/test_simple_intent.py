from __future__ import annotations

from voxera.core.simple_intent import (
    check_skill_family_mismatch,
    classify_simple_operator_intent,
)


def test_open_terminal_direct_deterministic():
    for goal in ("open terminal", "open the terminal", "please open terminal"):
        r = classify_simple_operator_intent(goal=goal)
        assert r.intent_kind == "open_terminal"
        assert r.deterministic is True
        assert r.first_step_only is False
        assert r.allowed_skill_ids == frozenset({"system.open_app"})


def test_open_terminal_compound_preserves_remainder():
    r = classify_simple_operator_intent(goal="open terminal and write a script")
    assert r.intent_kind == "open_terminal"
    assert r.compound_action is True
    assert r.first_step_only is True
    assert r.first_action_intent_kind == "open_terminal"
    assert r.trailing_remainder == "write a script"


def test_open_terminal_meta_not_actionable():
    for goal in (
        "write a script that opens terminal",
        "how do I open terminal",
        "why does open terminal open hello world",
        "the phrase open terminal is being misrouted",
    ):
        r = classify_simple_operator_intent(goal=goal)
        assert r.intent_kind == "unknown_or_ambiguous"
        assert r.deterministic is False


def test_open_url_rules():
    yes = classify_simple_operator_intent(goal="open this URL: https://example.com")
    assert yes.intent_kind == "open_url"
    assert yes.extracted_target == "https://example.com"

    comp = classify_simple_operator_intent(goal="open https://example.com and summarize it")
    assert comp.intent_kind == "open_url"
    assert comp.first_step_only is True
    assert comp.trailing_remainder == "summarize it"

    for goal in (
        "what is this link https://example.com",
        "summarize https://example.com",
        "here is the docs link https://example.com",
    ):
        r = classify_simple_operator_intent(goal=goal)
        assert r.intent_kind != "open_url"


def test_open_app_rules():
    for goal in ("open calculator", "launch calculator", "open firefox"):
        r = classify_simple_operator_intent(goal=goal)
        assert r.intent_kind == "open_app"
        assert r.allowed_skill_ids == frozenset({"system.open_app"})

    comp = classify_simple_operator_intent(goal="open calculator and create a note")
    assert comp.intent_kind == "open_app"
    assert comp.first_step_only is True
    assert comp.trailing_remainder == "create a note"

    for goal in ("open an app", "open something", "help me open calculator"):
        r = classify_simple_operator_intent(goal=goal)
        assert r.intent_kind == "unknown_or_ambiguous"


def test_read_write_regression_preserved():
    w = classify_simple_operator_intent(goal="write a file called text.txt")
    assert w.intent_kind == "write_file"
    assert "files.write_text" in w.allowed_skill_ids

    rd = classify_simple_operator_intent(goal="read the file ~/VoxeraOS/notes/a.txt")
    assert rd.intent_kind == "read_file"
    assert rd.extracted_target == "~/VoxeraOS/notes/a.txt"


def test_first_step_mismatch_fail_closed_for_open_url_and_open_app():
    open_url = classify_simple_operator_intent(goal="open https://example.com and summarize it")
    mismatch, reason = check_skill_family_mismatch(open_url, "system.open_app")
    assert mismatch is True
    assert reason == "simple_intent_skill_family_mismatch"

    open_app = classify_simple_operator_intent(goal="open calculator and create a note")
    mismatch2, reason2 = check_skill_family_mismatch(open_app, "system.open_url")
    assert mismatch2 is True
    assert reason2 == "simple_intent_skill_family_mismatch"


def test_first_step_match_for_compound_open_terminal():
    intent = classify_simple_operator_intent(goal="open terminal then run ls")
    ok, reason = check_skill_family_mismatch(intent, "system.open_app")
    assert ok is False
    assert reason == "skill_family_matches"
