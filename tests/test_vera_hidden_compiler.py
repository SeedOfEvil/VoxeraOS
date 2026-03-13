from __future__ import annotations

import asyncio
from types import SimpleNamespace

from voxera.vera import service as vera_service


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _mock_config_with_brain(monkeypatch) -> None:
    provider = SimpleNamespace(api_key_ref="test-key")
    monkeypatch.setattr(
        vera_service,
        "load_app_config",
        lambda: SimpleNamespace(brain={"primary": provider}),
    )


def test_extract_hidden_compiler_decision_replace_preview():
    decision = vera_service._extract_hidden_compiler_decision(
        '{"action":"replace_preview","intent_type":"new_intent","updated_preview":{"goal":"open https://example.com"},"patch":null}'
    )
    assert decision is not None
    assert decision.action == "replace_preview"


def test_extract_hidden_compiler_decision_rejects_invalid_contract():
    decision = vera_service._extract_hidden_compiler_decision(
        '{"action":"replace_preview","intent_type":"new_intent","patch":{"goal":"x"}}'
    )
    assert decision is None


def test_generate_preview_builder_update_applies_patch_refinement(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"patch_preview","intent_type":"refinement","updated_preview":null,"patch":{"write_file":{"mode":"append"}}}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[{"role": "user", "text": "write a file called jokes.txt with a funny joke"}],
            user_message="make it append instead",
            active_preview={
                "goal": "write a file called jokes.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/jokes.txt",
                    "content": "joke",
                    "mode": "overwrite",
                },
            },
        )
    )

    assert preview is not None
    assert preview["write_file"]["mode"] == "append"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokes.txt"


def test_generate_preview_builder_update_no_change_preserves_preview(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"no_change","intent_type":"unclear","updated_preview":null,"patch":null}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    active = {"goal": "open https://example.com"}
    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="hmm change that maybe",
            active_preview=active,
        )
    )

    assert preview == active


def test_generate_preview_builder_update_invalid_model_output_falls_back_to_deterministic(
    monkeypatch,
):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse('{"not":"valid"}')

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="open cnn.com",
            active_preview=None,
        )
    )

    assert preview == {"goal": "open https://cnn.com"}


def test_generate_preview_builder_update_refinement_from_pronoun_patch(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"patch_preview","intent_type":"refinement","updated_preview":null,"patch":{"write_file":{"content":"Why do programmers prefer dark mode? Because light attracts bugs."}}}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[{"role": "user", "text": "make a file called jokes.txt with a funny joke"}],
            user_message="actually make it a programmer joke",
            active_preview={
                "goal": "write a file called jokes.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/jokes.txt",
                    "content": "old",
                    "mode": "overwrite",
                },
            },
        )
    )

    assert preview is not None
    assert "dark mode" in preview["write_file"]["content"]
    assert preview["write_file"]["mode"] == "overwrite"


def test_generate_preview_builder_update_no_change_uses_safe_deterministic_refinement(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"no_change","intent_type":"unclear","updated_preview":null,"patch":null}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="call it funnierjoke.txt instead",
            active_preview={
                "goal": "write a file called jokes.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/jokes.txt",
                    "content": "hello",
                    "mode": "overwrite",
                },
            },
        )
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/funnierjoke.txt"


def test_generate_preview_builder_update_no_change_uses_semantic_news_content_refinement(
    monkeypatch,
):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"no_change","intent_type":"unclear","updated_preview":null,"patch":null}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="make the content a summary of today's top news",
            active_preview={
                "goal": "write a file called testnews.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/testnews.txt",
                    "content": "old",
                    "mode": "overwrite",
                },
            },
        )
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/testnews.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert "summary" in preview["write_file"]["content"].lower()
    assert "news" in preview["write_file"]["content"].lower()


def test_generate_preview_builder_update_append_instead_switches_mode(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"no_change","intent_type":"unclear","updated_preview":null,"patch":null}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="append instead",
            active_preview={
                "goal": "write a file called log.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/log.txt",
                    "content": "some content",
                    "mode": "overwrite",
                },
            },
        )
    )

    assert preview is not None
    assert preview["write_file"]["mode"] == "append"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/log.txt"
    assert preview["write_file"]["content"] == "some content"


def test_generate_preview_builder_update_no_change_put_that_into_file_is_fail_closed(monkeypatch):
    _mock_config_with_brain(monkeypatch)

    class _FakeBrain:
        def __init__(self, model, api_key_ref):
            _ = (model, api_key_ref)

        async def generate(self, messages, tools):
            _ = (messages, tools)
            return _FakeResponse(
                '{"action":"no_change","intent_type":"unclear","updated_preview":null,"patch":null}'
            )

    monkeypatch.setattr(vera_service, "GeminiBrain", _FakeBrain)

    active = {
        "goal": "write a file called testnews.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/testnews.txt",
            "content": "old",
            "mode": "overwrite",
        },
    }
    preview = asyncio.run(
        vera_service.generate_preview_builder_update(
            turns=[],
            user_message="put that into the file",
            active_preview=active,
        )
    )

    assert preview == active
