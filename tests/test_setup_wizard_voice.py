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
    def test_voice_step_announces_immediate_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_configure_voice must warn the operator that answers persist before the final YAML confirm."""
        cfg_path = tmp_path / "config.json"
        _monkey_confirm(monkeypatch, [False])
        _monkey_prompt(monkeypatch, [])

        setup_wizard._configure_voice(runtime_config_path=cfg_path)

        out = capsys.readouterr().out
        assert "config.json" in out
        # Must make clear the write happens before the final write-config prompt.
        assert "before" in out.lower()

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
                    "voice_stt_whisper_model": "distil-whisper/distil-large-v3",
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
        # The whisper model selection must be cleared too so declining the
        # foundation never leaves orphan STT state behind.
        assert "voice_stt_whisper_model" not in data
        # Unrelated runtime keys must be preserved.
        assert data["panel_port"] == 8844

    def test_enabling_foundation_preserves_existing_whisper_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-running setup with STT enabled must not wipe a panel-saved model."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": "whisper_local",
                    "voice_stt_whisper_model": "distil-whisper/distil-large-v3",
                }
            ),
            encoding="utf-8",
        )

        _monkey_confirm(
            monkeypatch,
            [
                True,  # Enable voice foundation?
                True,  # Enable STT?
                False,  # Enable TTS?
            ],
        )
        _monkey_prompt(monkeypatch, ["whisper_local"])

        setup_wizard._configure_voice(runtime_config_path=cfg_path)

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["voice_stt_whisper_model"] == "distil-whisper/distil-large-v3"


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

    def test_rerun_prefills_existing_piper_model_as_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-running setup with an existing piper_model keeps it when the user presses Enter."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "piper_local",
                    "voice_tts_piper_model": "/models/existing-voice.onnx",
                }
            ),
            encoding="utf-8",
        )
        _monkey_confirm(monkeypatch, [True, False, True])

        captured_defaults: list[object] = []
        answer_queue = iter(["piper_local", "/models/existing-voice.onnx"])

        def _ask(prompt, **kwargs):
            captured_defaults.append(kwargs.get("default"))
            return next(answer_queue)

        monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        # The Piper-model prompt must have been offered the existing value as default.
        assert "/models/existing-voice.onnx" in captured_defaults
        # The existing value is preserved, not wiped, on an Enter-press re-run.
        assert answers["voice_tts_piper_model"] == "/models/existing-voice.onnx"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["voice_tts_piper_model"] == "/models/existing-voice.onnx"

    def test_rerun_explicit_default_clears_piper_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Typing 'default' at the Piper prompt clears the stored model."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "piper_local",
                    "voice_tts_piper_model": "/models/existing-voice.onnx",
                }
            ),
            encoding="utf-8",
        )
        _monkey_confirm(monkeypatch, [True, False, True])
        _monkey_prompt(monkeypatch, ["piper_local", "default"])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["voice_tts_piper_model"] is None
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "voice_tts_piper_model" not in data

    def test_empty_runtime_config_file_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 0-byte config.json (`touch config.json`) must not crash voice setup."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("", encoding="utf-8")

        _monkey_confirm(monkeypatch, [False])  # decline foundation
        _monkey_prompt(monkeypatch, [])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["enable_voice_foundation"] is False
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["enable_voice_foundation"] is False

    def test_missing_runtime_config_file_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No file at all is the first-run case; setup must create it cleanly."""
        cfg_path = tmp_path / "config.json"
        assert not cfg_path.exists()

        _monkey_confirm(monkeypatch, [True, True, False])
        _monkey_prompt(monkeypatch, ["whisper_local"])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        assert answers["voice_stt_backend"] == "whisper_local"
        assert cfg_path.exists()

    def test_malformed_runtime_config_skips_voice_without_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Invalid JSON must NOT raise and must NOT overwrite the user's file."""
        cfg_path = tmp_path / "config.json"
        original = "{this is: not json,"
        cfg_path.write_text(original, encoding="utf-8")

        # No prompts should fire -- the wizard should short-circuit before asking.
        recorded_confirms = _monkey_confirm(monkeypatch, [])
        recorded_prompts = _monkey_prompt(monkeypatch, [])

        answers = setup_wizard._configure_voice(runtime_config_path=cfg_path)

        # Skipped: all voice keys reflect "not enabled", nothing written.
        assert answers["enable_voice_foundation"] is False
        assert recorded_confirms == []
        assert recorded_prompts == []
        # Operator file is NOT clobbered.
        assert cfg_path.read_text(encoding="utf-8") == original
        # Clean operator-facing message was rendered (no raw traceback).
        out = capsys.readouterr().out
        assert "Voice Setup skipped" in out
        assert "not valid JSON" in out
        assert "voxera setup" in out

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
