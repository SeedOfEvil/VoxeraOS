"""Tests for the voice configuration step added to the setup wizard.

Pins the brand-new-user flow: ``_configure_voice`` persists answers to
the runtime config JSON (``~/.config/voxera/config.json``), never leaves
stale voice keys behind when the operator declines the foundation, and
honors the Piper-model prompt only when Piper TTS is selected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera import setup_wizard


def _monkey_prompt(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Wire rich.Prompt.ask to replay answers in order; return the recorded prompts."""
    recorded_prompts: list[str] = []
    iterator = iter(answers)

    def _ask(prompt, **kwargs):
        recorded_prompts.append(str(prompt))
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(
                f"Prompt ran out of scripted answers at {prompt!r}; "
                f"recorded so far: {recorded_prompts}"
            ) from exc

    monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)
    return recorded_prompts


def _monkey_confirm(monkeypatch: pytest.MonkeyPatch, answers: list[bool]) -> list[str]:
    recorded_prompts: list[str] = []
    iterator = iter(answers)

    def _confirm(prompt, **kwargs):
        recorded_prompts.append(str(prompt))
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(
                f"Confirm ran out of scripted answers at {prompt!r}; "
                f"recorded so far: {recorded_prompts}"
            ) from exc

    monkeypatch.setattr(setup_wizard.Confirm, "ask", _confirm)
    return recorded_prompts


class TestConfigureVoiceDeclined:
    def test_declining_foundation_writes_all_voice_keys_as_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "config.json"
        _monkey_confirm(monkeypatch, [False])  # Enable voice foundation? -> No
        _monkey_prompt(monkeypatch, [])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["enable_voice_foundation"] is False
        assert answers["enable_voice_input"] is False
        assert answers["enable_voice_output"] is False
        assert answers["voice_stt_backend"] is None
        assert answers["voice_tts_backend"] is None
        assert answers["voice_tts_piper_model"] is None

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["enable_voice_foundation"] is False
        assert data["enable_voice_input"] is False
        assert data["enable_voice_output"] is False
        # None-valued keys are pruned from the file (cannot leave stale backend).
        assert "voice_stt_backend" not in data
        assert "voice_tts_backend" not in data
        assert "voice_tts_piper_model" not in data

    def test_declining_foundation_clears_previously_written_voice_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "enable_voice_output": True,
                    "voice_stt_backend": "whisper_local",
                    "voice_tts_backend": "piper_local",
                    "voice_tts_piper_model": "/tmp/model.onnx",
                    "panel_port": 8844,
                }
            ),
            encoding="utf-8",
        )

        _monkey_confirm(monkeypatch, [False])
        _monkey_prompt(monkeypatch, [])

        setup_wizard._configure_voice(runtime_config_path=cfg_path)

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["enable_voice_foundation"] is False
        assert "voice_stt_backend" not in data
        assert "voice_tts_backend" not in data
        assert "voice_tts_piper_model" not in data
        # Unrelated runtime keys must be preserved.
        assert data["panel_port"] == 8844


class TestConfigureVoiceEnabled:
    def test_enable_foundation_and_stt_only_writes_stt_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "config.json"
        _monkey_confirm(
            monkeypatch,
            [
                True,  # Enable voice foundation?
                True,  # Enable STT?
                False,  # Enable TTS?
            ],
        )
        _monkey_prompt(monkeypatch, ["whisper_local"])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["enable_voice_foundation"] is True
        assert answers["enable_voice_input"] is True
        assert answers["voice_stt_backend"] == "whisper_local"
        assert answers["enable_voice_output"] is False
        assert answers["voice_tts_backend"] is None
        assert answers["voice_tts_piper_model"] is None

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["voice_stt_backend"] == "whisper_local"
        assert "voice_tts_backend" not in data

    def test_enable_tts_piper_prompts_for_model_and_persists_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "config.json"
        _monkey_confirm(
            monkeypatch,
            [
                True,  # Enable voice foundation?
                False,  # Enable STT?
                True,  # Enable TTS?
            ],
        )
        _monkey_prompt(monkeypatch, ["piper_local", "/models/en_US-voice.onnx"])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["voice_tts_backend"] == "piper_local"
        assert answers["voice_tts_piper_model"] == "/models/en_US-voice.onnx"

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["voice_tts_piper_model"] == "/models/en_US-voice.onnx"

    def test_enable_tts_piper_with_blank_model_keeps_piper_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = tmp_path / "config.json"
        _monkey_confirm(
            monkeypatch,
            [
                True,  # Enable voice foundation?
                False,  # Enable STT?
                True,  # Enable TTS?
            ],
        )
        _monkey_prompt(monkeypatch, ["piper_local", ""])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["voice_tts_backend"] == "piper_local"
        assert answers["voice_tts_piper_model"] is None

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "voice_tts_piper_model" not in data

    def test_voice_answers_load_back_through_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After setup writes config, load_voice_foundation_flags() reads it back truthfully."""
        from voxera.voice.flags import load_voice_foundation_flags

        cfg_path = tmp_path / "config.json"
        _monkey_confirm(monkeypatch, [True, True, True])
        _monkey_prompt(
            monkeypatch,
            ["whisper_local", "piper_local", "en_US-amy-medium"],
        )
        setup_wizard._configure_voice(runtime_config_path=cfg_path)

        flags = load_voice_foundation_flags(config_path=cfg_path, environ={})
        assert flags.enable_voice_foundation is True
        assert flags.voice_input_enabled is True
        assert flags.voice_output_enabled is True
        assert flags.voice_stt_backend == "whisper_local"
        assert flags.voice_tts_backend == "piper_local"
        assert flags.voice_tts_piper_model == "en_US-amy-medium"
