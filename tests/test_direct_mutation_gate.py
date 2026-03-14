"""Tests for the queue-first direct CLI mutation gate.

Verifies that:
- read-only skills execute directly via CLI
- mutating skills are blocked by default
- dev-mode override works only with VOXERA_DEV_MODE=1
- messaging is clear and actionable
"""

from __future__ import annotations

import click.exceptions
import pytest

from voxera.cli_skills_missions import _effect_classes_for, _is_dev_mode, run_impl
from voxera.models import SkillManifest
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner, is_skill_read_only

# ── is_skill_read_only unit tests ──────────────────────────────────────


def _manifest(*, capabilities, **kwargs):
    defaults = dict(
        id="test.skill",
        name="Test Skill",
        description="Test description",
        entrypoint="voxera_builtin_skills.system_status:run",
    )
    defaults.update(kwargs)
    return SkillManifest(capabilities=capabilities, **defaults)


class TestIsSkillReadOnly:
    def test_read_only_single_cap(self):
        assert is_skill_read_only(_manifest(capabilities=["state.read"])) is True

    def test_read_only_multiple_caps(self):
        assert is_skill_read_only(_manifest(capabilities=["state.read", "files.read"])) is True

    def test_mutating_write_cap(self):
        assert is_skill_read_only(_manifest(capabilities=["files.write"])) is False

    def test_mutating_execute_cap(self):
        assert is_skill_read_only(_manifest(capabilities=["sandbox.exec"])) is False

    def test_mixed_read_and_write(self):
        assert is_skill_read_only(_manifest(capabilities=["files.read", "files.write"])) is False

    def test_empty_capabilities_fail_closed(self):
        assert is_skill_read_only(_manifest(capabilities=[])) is False

    def test_unknown_capability_fail_closed(self):
        assert is_skill_read_only(_manifest(capabilities=["unknown.cap"])) is False


# ── _is_dev_mode unit tests ───────────────────────────────────────────


class TestIsDevMode:
    def test_unset(self, monkeypatch):
        monkeypatch.delenv("VOXERA_DEV_MODE", raising=False)
        assert _is_dev_mode() is False

    def test_empty(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "")
        assert _is_dev_mode() is False

    def test_truthy_1(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "1")
        assert _is_dev_mode() is True

    def test_truthy_true(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "true")
        assert _is_dev_mode() is True

    def test_truthy_yes(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "YES")
        assert _is_dev_mode() is True

    def test_falsy_0(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "0")
        assert _is_dev_mode() is False

    def test_falsy_no(self, monkeypatch):
        monkeypatch.setenv("VOXERA_DEV_MODE", "no")
        assert _is_dev_mode() is False


# ── run_impl integration tests ────────────────────────────────────────


def _noop_config():
    from voxera.config import AppConfig

    return AppConfig()


def _stub_registry():
    """Build a registry with test manifests pre-loaded."""
    reg = SkillRegistry()
    reg.discover()
    return reg


class TestRunImplMutationGate:
    """Integration tests for the mutation gate in run_impl."""

    def test_read_only_skill_runs_directly(self, monkeypatch):
        """Read-only skills should execute without any gate."""
        monkeypatch.delenv("VOXERA_DEV_MODE", raising=False)

        # system.status is read-only (state.read)
        # We need to capture the output; if it doesn't raise, it ran
        ran = False

        class FakeRunner(SkillRunner):
            def run(self, manifest, args, policy, require_approval_cb=None, audit_context=None):
                nonlocal ran
                ran = True
                from voxera.models import RunResult

                return RunResult(ok=True, output="ok")

        run_impl(
            load_config=_noop_config,
            skill_registry_cls=lambda: _stub_registry(),
            skill_runner_cls=lambda reg: FakeRunner(reg),
            approval_prompt=lambda *a, **kw: True,
            skill_id="system.status",
            arg=None,
            dry_run=False,
            allow_direct_mutation=False,
        )
        assert ran is True

    def test_mutating_skill_blocked_by_default(self, monkeypatch):
        """Mutating skills should be blocked without dev mode or flag."""
        monkeypatch.delenv("VOXERA_DEV_MODE", raising=False)

        with pytest.raises((SystemExit, click.exceptions.Exit)):
            run_impl(
                load_config=_noop_config,
                skill_registry_cls=lambda: _stub_registry(),
                skill_runner_cls=lambda reg: SkillRunner(reg),
                approval_prompt=lambda *a, **kw: True,
                skill_id="files.write_text",
                arg=None,
                dry_run=False,
                allow_direct_mutation=False,
            )

    def test_mutating_skill_blocked_with_flag_but_no_dev_mode(self, monkeypatch):
        """--allow-direct-mutation without VOXERA_DEV_MODE=1 should still block."""
        monkeypatch.delenv("VOXERA_DEV_MODE", raising=False)

        with pytest.raises((SystemExit, click.exceptions.Exit)):
            run_impl(
                load_config=_noop_config,
                skill_registry_cls=lambda: _stub_registry(),
                skill_runner_cls=lambda reg: SkillRunner(reg),
                approval_prompt=lambda *a, **kw: True,
                skill_id="files.write_text",
                arg=None,
                dry_run=False,
                allow_direct_mutation=True,
            )

    def test_mutating_skill_allowed_with_dev_mode_and_flag(self, monkeypatch):
        """With VOXERA_DEV_MODE=1 and --allow-direct-mutation, mutating skills run."""
        monkeypatch.setenv("VOXERA_DEV_MODE", "1")

        ran = False

        class FakeRunner(SkillRunner):
            def run(self, manifest, args, policy, require_approval_cb=None, audit_context=None):
                nonlocal ran
                ran = True
                from voxera.models import RunResult

                return RunResult(ok=True, output="ok")

        run_impl(
            load_config=_noop_config,
            skill_registry_cls=lambda: _stub_registry(),
            skill_runner_cls=lambda reg: FakeRunner(reg),
            approval_prompt=lambda *a, **kw: True,
            skill_id="files.write_text",
            arg=None,
            dry_run=False,
            allow_direct_mutation=True,
        )
        assert ran is True

    def test_dry_run_bypasses_mutation_gate(self, monkeypatch, capsys):
        """--dry-run should work for mutating skills without the gate blocking."""
        monkeypatch.delenv("VOXERA_DEV_MODE", raising=False)

        # dry_run should not raise even for mutating skills
        run_impl(
            load_config=_noop_config,
            skill_registry_cls=lambda: _stub_registry(),
            skill_runner_cls=lambda reg: SkillRunner(reg),
            approval_prompt=lambda *a, **kw: True,
            skill_id="files.write_text",
            arg=None,
            dry_run=True,
            allow_direct_mutation=False,
        )


# ── Real built-in skill classification tests ──────────────────────────


class TestBuiltinSkillClassification:
    """Verify that real built-in skills are classified correctly."""

    def _get_manifest(self, skill_id):
        reg = SkillRegistry()
        reg.discover()
        return reg.get(skill_id)

    def test_system_status_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("system.status")) is True

    def test_files_list_dir_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("files.list_dir")) is True

    def test_files_exists_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("files.exists")) is True

    def test_files_stat_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("files.stat")) is True

    def test_window_list_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("system.window_list")) is True

    def test_clipboard_paste_is_read_only(self):
        assert is_skill_read_only(self._get_manifest("clipboard.paste")) is True

    def test_files_write_text_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("files.write_text")) is False

    def test_files_delete_file_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("files.delete_file")) is False

    def test_sandbox_exec_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("sandbox.exec")) is False

    def test_system_open_app_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("system.open_app")) is False

    def test_clipboard_copy_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("clipboard.copy")) is False

    def test_files_copy_file_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("files.copy_file")) is False

    def test_system_set_volume_is_mutating(self):
        assert is_skill_read_only(self._get_manifest("system.set_volume")) is False


# ── _effect_classes_for helper tests ──────────────────────────────────


class TestEffectClassesFor:
    def test_read_only_skill(self):
        assert _effect_classes_for(_manifest(capabilities=["state.read"])) == "read"

    def test_write_skill(self):
        assert _effect_classes_for(_manifest(capabilities=["files.write"])) == "write"

    def test_mixed_skill(self):
        result = _effect_classes_for(_manifest(capabilities=["files.read", "files.write"]))
        assert "read" in result
        assert "write" in result
