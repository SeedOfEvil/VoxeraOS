from pathlib import Path

from rich.text import Text

from voxera import setup_wizard
from voxera.models import AppConfig, BrainConfig


def _provider() -> setup_wizard.ProviderChoice:
    return setup_wizard.ProviderChoice(
        slug="openrouter",
        label="OpenRouter",
        env_ref="OPENROUTER_API_KEY",
        brain_type="openai_compat",
        default_model="google/gemini-3-flash-preview",
    )


def test_provider_key_choice_keep_returns_existing(monkeypatch):
    calls: list[str] = []

    def _ask(prompt: str, **kwargs):
        calls.append(prompt)
        return "keep"

    monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved == "OPENROUTER_API_KEY"
    assert isinstance(calls[0], Text)
    assert calls[0].plain == "Auth for OpenRouter [keep/skip/replace]"


def test_provider_key_choice_skip_clears_existing(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "skip")

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved is None


def test_provider_key_choice_replace_uses_new_reference(monkeypatch):
    answers = iter(["replace", "OPENROUTER_ALT_KEY"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved == "OPENROUTER_ALT_KEY"


def test_provider_key_choice_set_when_missing(monkeypatch):
    calls: list[str] = []
    answers = iter(["set", "OPENROUTER_API_KEY"])

    def _ask(prompt: str, **kwargs):
        calls.append(prompt)
        return next(answers)

    monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)

    resolved = setup_wizard._apply_provider_key_choice(_provider(), existing_ref=None)

    assert resolved == "OPENROUTER_API_KEY"
    assert isinstance(calls[0], Text)
    assert calls[0].plain == "Auth for OpenRouter [skip/set]"


def test_pick_openrouter_model_prefers_recommended(monkeypatch):
    monkeypatch.setattr(
        setup_wizard,
        "load_curated_openrouter_catalog",
        lambda: [
            {
                "vendor": "OpenAI",
                "id": "google/gemini-3-flash-preview",
                "name": "Gemini 3 Flash",
                "context_length": 128000,
                "pricing_prompt": "0.15",
                "pricing_completion": "0.60",
            }
        ],
    )
    monkeypatch.setattr(setup_wizard, "grouped_catalog", lambda models: [("OpenAI", models)])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "recommended")

    model = setup_wizard._pick_openrouter_model("primary")

    assert model == "google/gemini-3-flash-preview"


def test_pick_openrouter_model_manual_path(monkeypatch):
    monkeypatch.setattr(
        setup_wizard,
        "load_curated_openrouter_catalog",
        lambda: [
            {
                "vendor": "OpenAI",
                "id": "google/gemini-3-flash-preview",
                "name": "Gemini 3 Flash",
            }
        ],
    )
    monkeypatch.setattr(setup_wizard, "grouped_catalog", lambda models: [("OpenAI", models)])
    answers = iter(["manual", "openai/gpt-4.1-mini"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))

    model = setup_wizard._pick_openrouter_model("primary")

    assert model == "openai/gpt-4.1-mini"


def test_configure_cloud_brains_runs_sequential_slots(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="a"),
            "fast": BrainConfig(type="openai_compat", model="b"),
            "reasoning": BrainConfig(type="openai_compat", model="c"),
            "fallback": BrainConfig(type="openai_compat", model="d"),
        }
    )
    seen: list[str] = []

    monkeypatch.setattr(setup_wizard, "_provider_summary_table", lambda: "providers")

    def _slot(cfg_obj, *, slot, existing):
        assert existing is not None
        seen.append(slot.key)

    monkeypatch.setattr(setup_wizard, "_configure_brain_slot", _slot)

    setup_wizard._configure_cloud_brains(cfg)

    assert seen == ["primary", "fast", "reasoning", "fallback"]


def test_launch_choice_opens_selected_panels(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "both")
    opened: list[str] = []
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda url: opened.append(url) or True)

    setup_wizard._launch_choice(service_state={"failed": [], "started": []})

    assert opened == ["http://127.0.0.1:8844/", "http://127.0.0.1:8790/"]


def test_ensure_runtime_services_running_success(monkeypatch):
    calls: list[tuple[str, ...]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _systemctl(*args: str):
        calls.append(args)
        if args[0] == "daemon-reload":
            return Result(0)
        if args[0] == "enable":
            return Result(0)
        if args[0] == "is-active":
            return Result(0)
        return Result(1, stderr="unexpected")

    monkeypatch.setattr(setup_wizard, "_systemctl_user", _systemctl)

    state = setup_wizard._ensure_runtime_services_running()

    assert state["failed"] == []
    assert state["started"] == list(setup_wizard.RUNTIME_SERVICE_UNITS)
    assert ("daemon-reload",) in calls


def test_ensure_runtime_services_running_reports_failures(monkeypatch):
    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _systemctl(*args: str):
        if args[:3] == ("enable", "--now", "voxera-vera.service"):
            return Result(1, stderr="boom")
        if args[0] in {"daemon-reload", "enable", "is-active"}:
            return Result(0)
        return Result(1)

    monkeypatch.setattr(setup_wizard, "_systemctl_user", _systemctl)

    state = setup_wizard._ensure_runtime_services_running()

    assert "voxera-vera.service" in state["failed"]


def test_launch_choice_skips_unavailable_service(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "vera")
    opened: list[str] = []
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda url: opened.append(url) or True)

    setup_wizard._launch_choice(service_state={"failed": ["voxera-vera.service"], "started": []})

    assert opened == []


def test_confirm_write_config_defaults_to_keep_existing(tmp_path, monkeypatch):
    existing = tmp_path / "config.yml"
    existing.write_text("mode: mixed\n", encoding="utf-8")

    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: False)

    assert setup_wizard._confirm_write_config(existing) is False


def test_confirm_write_config_true_when_missing(tmp_path):
    missing = Path(tmp_path / "missing.yml")
    assert setup_wizard._confirm_write_config(missing) is True


def test_run_setup_finish_path_ensures_services_before_launch(monkeypatch):
    import asyncio

    monkeypatch.setattr(setup_wizard, "ensure_dirs", lambda: None)
    monkeypatch.setattr(setup_wizard, "_pick_mode", lambda: "mixed")
    monkeypatch.setattr(setup_wizard, "_pick_brain_type", lambda: "cloud")
    monkeypatch.setattr(setup_wizard, "_configure_cloud_brains", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_configure_web_investigation", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_policy_defaults", lambda: setup_wizard.PolicyApprovals())
    monkeypatch.setattr(setup_wizard, "_confirm_write_config", lambda path: True)
    monkeypatch.setattr(setup_wizard, "save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard, "save_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_wizard, "capabilities_report_path", lambda: Path("/tmp/voxera-cap.json")
    )

    calls: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_post_setup_validation",
        lambda cfg: calls.append("validation"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_ensure_runtime_services_running",
        lambda: calls.append("ensure") or {"failed": [], "started": []},
    )

    def _launch(*, service_state):
        assert service_state == {"failed": [], "started": []}
        calls.append("launch")

    monkeypatch.setattr(setup_wizard, "_launch_choice", _launch)
    monkeypatch.setattr(setup_wizard, "_print_what_next", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    asyncio.run(setup_wizard.run_setup())

    assert calls == ["validation", "ensure", "launch"]


def test_configure_web_investigation_disabled(monkeypatch):
    cfg = AppConfig()
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: False)

    setup_wizard._configure_web_investigation(cfg)

    assert cfg.web_investigation is None


def test_configure_web_investigation_persists_secret_ref_and_max_results(monkeypatch):
    cfg = AppConfig()
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)
    answers = iter(["brave-live-key", "7"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))

    stored: dict[str, str] = {}

    def _set_secret(ref: str, value: str) -> str:
        stored["ref"] = ref
        stored["value"] = value
        return "keyring:BRAVE_API_KEY"

    monkeypatch.setattr(setup_wizard, "set_secret", _set_secret)
    monkeypatch.setattr(setup_wizard.console, "print", lambda *args, **kwargs: None)

    setup_wizard._configure_web_investigation(cfg)

    assert stored == {"ref": "BRAVE_API_KEY", "value": "brave-live-key"}
    assert cfg.web_investigation is not None
    assert cfg.web_investigation.provider == "brave"
    assert cfg.web_investigation.api_key_ref == "BRAVE_API_KEY"
    assert cfg.web_investigation.env_api_key_var == "BRAVE_API_KEY"
    assert cfg.web_investigation.max_results == 7


# --- Post-setup validation tests ---


def test_check_brain_config_primary_usable(monkeypatch):
    """Single usable slot → one ok check naming the usable slot."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="m1", api_key_ref="OPENROUTER_API_KEY"
            )
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert checks[0]["check"] == "brain readiness"
    assert "primary" in checks[0]["detail"]


def test_check_brain_config_no_key_ref_only_slot():
    """Single slot with no api_key_ref and no other slots → warn."""
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref=None)}
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "warn"
    assert "No brain slot has an API key" in checks[0]["detail"]


def test_check_brain_config_key_not_resolved(monkeypatch):
    """Only slot has a key ref but it can't be resolved → warn with fix hint."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(setup_wizard, "get_secret", lambda ref: None)
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="m1", api_key_ref="OPENROUTER_API_KEY"
            )
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "warn"
    assert "OPENROUTER_API_KEY" in checks[0]["detail"]
    assert "not found" in checks[0]["detail"]
    assert "voxera secrets set OPENROUTER_API_KEY" in checks[0]["hint"]


def test_check_brain_config_key_found_via_keyring(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(setup_wizard, "get_secret", lambda ref: "sk-keyring-value")
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="MY_KEY")}
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert "primary" in checks[0]["detail"]


def test_check_brain_config_no_brains():
    cfg = AppConfig(brain={})

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "warn"
    assert "No brain slots" in checks[0]["detail"]


def test_check_brain_config_primary_usable_optional_unconfigured(monkeypatch):
    """Primary is usable — unconfigured optional slots do NOT produce warnings."""
    monkeypatch.setenv("GOOD_KEY", "sk-valid")
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="GOOD_KEY"),
            "fast": BrainConfig(type="openai_compat", model="m2", api_key_ref=None),
            "reasoning": BrainConfig(type="openai_compat", model="m3", api_key_ref=None),
            "fallback": BrainConfig(type="openai_compat", model="m4", api_key_ref=None),
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert checks[0]["check"] == "brain readiness"
    assert "primary" in checks[0]["detail"]
    # No per-slot warnings for unconfigured optional slots.
    warn_checks = [c for c in checks if c["status"] == "warn"]
    assert warn_checks == []


def test_check_brain_config_primary_usable_optional_broken_still_ok(monkeypatch):
    """Primary is usable — broken optional slots do NOT produce warnings."""
    monkeypatch.setenv("GOOD_KEY", "sk-valid")
    monkeypatch.delenv("BAD_KEY", raising=False)
    monkeypatch.setattr(setup_wizard, "get_secret", lambda ref: None)
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="GOOD_KEY"),
            "fast": BrainConfig(type="openai_compat", model="m2", api_key_ref="BAD_KEY"),
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert "primary" in checks[0]["detail"]


def test_check_brain_config_no_usable_path_shows_first_broken(monkeypatch):
    """No usable slot → one warn with the first broken key's fix hint."""
    monkeypatch.delenv("KEY_A", raising=False)
    monkeypatch.delenv("KEY_B", raising=False)
    monkeypatch.setattr(setup_wizard, "get_secret", lambda ref: None)
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="KEY_A"),
            "fast": BrainConfig(type="openai_compat", model="m2", api_key_ref="KEY_B"),
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "warn"
    assert "KEY_A" in checks[0]["detail"]
    assert "voxera secrets set KEY_A" in checks[0]["hint"]


def test_check_brain_config_get_secret_exception(monkeypatch):
    """get_secret raising does not crash — slot treated as not found."""
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setattr(
        setup_wizard, "get_secret", lambda ref: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="MY_KEY")}
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "warn"
    assert "MY_KEY" in checks[0]["detail"]


def test_check_brain_config_lists_all_usable_slots(monkeypatch):
    """Multiple usable slots are listed in the ok detail."""
    monkeypatch.setenv("K1", "v")
    monkeypatch.setenv("K2", "v")
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="K1"),
            "fast": BrainConfig(type="openai_compat", model="m2", api_key_ref="K2"),
        }
    )

    checks = setup_wizard._check_brain_config(cfg)

    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert "primary" in checks[0]["detail"]
    assert "fast" in checks[0]["detail"]


def test_render_validation_summary_all_pass(capsys):
    checks = [
        {"check": "brain readiness", "status": "ok", "detail": "Usable: primary.", "hint": ""},
    ]

    setup_wizard._render_validation_summary(checks)
    out = capsys.readouterr().out

    assert "1 check passed" in out
    assert "Setup complete. Try: voxera vera" in out


def test_render_validation_summary_with_warnings(capsys):
    checks = [
        {
            "check": "brain readiness",
            "status": "warn",
            "detail": "KEY not found.",
            "hint": "Set KEY.",
        },
    ]

    setup_wizard._render_validation_summary(checks)
    out = capsys.readouterr().out

    assert "KEY not found" in out
    assert "Set KEY" in out
    assert "1 warning" in out
    assert "Setup complete. Try: voxera vera" not in out


def test_render_validation_summary_with_failures(capsys):
    checks = [
        {"check": "brain readiness", "status": "fail", "detail": "broken.", "hint": "Fix it."},
    ]

    setup_wizard._render_validation_summary(checks)
    out = capsys.readouterr().out

    assert "broken" in out
    assert "Fix it" in out
    assert "checks need attention" in out


def test_render_validation_summary_mixed_no_fake_success(capsys):
    checks = [
        {"check": "brain readiness", "status": "ok", "detail": "Usable: primary.", "hint": ""},
        {
            "check": "infra",
            "status": "warn",
            "detail": "degraded.",
            "hint": "Check logs.",
        },
    ]

    setup_wizard._render_validation_summary(checks)
    out = capsys.readouterr().out

    assert "1 check passed" in out
    assert "1 warning" in out
    assert "degraded" in out
    assert "Setup complete. Try: voxera vera" not in out


def test_post_setup_validation_suppresses_all_doctor_warns(monkeypatch, capsys):
    """All doctor warn checks are suppressed — only ok and fail pass through."""
    monkeypatch.setenv("TEST_KEY", "sk-test")
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="TEST_KEY")}
    )
    doctor_result = [
        {"check": "lock status", "status": "warn", "detail": "exists=False", "hint": ""},
        {
            "check": "recent history: last_ok",
            "status": "warn",
            "detail": "event=- ts=-",
            "hint": "No recent successful health event recorded.",
        },
        {
            "check": "voice: stt",
            "status": "warn",
            "detail": "unconfigured",
            "hint": "Set voice_stt_backend.",
        },
        {"check": "queue counts", "status": "ok", "detail": "all zero", "hint": ""},
    ]
    monkeypatch.setattr(setup_wizard, "run_quick_doctor", lambda: doctor_result)

    setup_wizard._post_setup_validation(cfg)
    out = capsys.readouterr().out

    # brain ok (1) + doctor ok (1) = 2 passed; all doctor warns dropped
    assert "2 checks passed" in out
    assert "lock status" not in out
    assert "last_ok" not in out
    assert "voice" not in out
    assert "Setup complete. Try: voxera vera" in out


def test_post_setup_validation_surfaces_doctor_fail(monkeypatch, capsys):
    """Doctor checks with status 'fail' DO surface in the post-setup summary."""
    monkeypatch.setenv("TEST_KEY", "sk-test")
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="TEST_KEY")}
    )
    doctor_result = [
        {
            "check": "critical: infra",
            "status": "fail",
            "detail": "disk full.",
            "hint": "Free disk space.",
        },
        {"check": "queue counts", "status": "ok", "detail": "all zero", "hint": ""},
    ]
    monkeypatch.setattr(setup_wizard, "run_quick_doctor", lambda: doctor_result)

    setup_wizard._post_setup_validation(cfg)
    out = capsys.readouterr().out

    assert "disk full" in out
    assert "Free disk space" in out
    assert "checks need attention" in out


def test_post_setup_validation_includes_doctor_ok_in_count(monkeypatch, capsys):
    """Doctor ok checks are counted but not individually displayed."""
    monkeypatch.setenv("TEST_KEY", "sk-test")
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="TEST_KEY")}
    )
    doctor_result = [{"check": "queue health", "status": "ok", "detail": "healthy", "hint": ""}]
    monkeypatch.setattr(setup_wizard, "run_quick_doctor", lambda: doctor_result)

    setup_wizard._post_setup_validation(cfg)
    out = capsys.readouterr().out

    assert "2 checks passed" in out
    assert "Setup complete. Try: voxera vera" in out


def test_post_setup_validation_handles_quick_doctor_failure(monkeypatch, capsys):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    cfg = AppConfig(
        brain={"primary": BrainConfig(type="openai_compat", model="m1", api_key_ref="TEST_KEY")}
    )
    monkeypatch.setattr(
        setup_wizard, "run_quick_doctor", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    setup_wizard._post_setup_validation(cfg)
    out = capsys.readouterr().out

    assert "1 check passed" in out
    assert "Setup complete. Try: voxera vera" in out


def test_run_setup_calls_post_setup_validation(monkeypatch):
    import asyncio

    monkeypatch.setattr(setup_wizard, "ensure_dirs", lambda: None)
    monkeypatch.setattr(setup_wizard, "_pick_mode", lambda: "mixed")
    monkeypatch.setattr(setup_wizard, "_pick_brain_type", lambda: "cloud")
    monkeypatch.setattr(setup_wizard, "_configure_cloud_brains", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_configure_web_investigation", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_policy_defaults", lambda: setup_wizard.PolicyApprovals())
    monkeypatch.setattr(setup_wizard, "_confirm_write_config", lambda path: True)
    monkeypatch.setattr(setup_wizard, "save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard, "save_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_wizard, "capabilities_report_path", lambda: Path("/tmp/voxera-cap.json")
    )
    monkeypatch.setattr(
        setup_wizard,
        "_ensure_runtime_services_running",
        lambda: {"failed": [], "started": []},
    )
    monkeypatch.setattr(setup_wizard, "_launch_choice", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard, "_print_what_next", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    validated: list[AppConfig] = []
    monkeypatch.setattr(setup_wizard, "_post_setup_validation", lambda cfg: validated.append(cfg))

    asyncio.run(setup_wizard.run_setup())

    assert len(validated) == 1
    assert isinstance(validated[0], AppConfig)


def test_run_setup_completes_with_validation_warnings(monkeypatch):
    import asyncio

    monkeypatch.setattr(setup_wizard, "ensure_dirs", lambda: None)
    monkeypatch.setattr(setup_wizard, "_pick_mode", lambda: "mixed")
    monkeypatch.setattr(setup_wizard, "_pick_brain_type", lambda: "cloud")
    monkeypatch.setattr(setup_wizard, "_configure_cloud_brains", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_configure_web_investigation", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_policy_defaults", lambda: setup_wizard.PolicyApprovals())
    monkeypatch.setattr(setup_wizard, "_confirm_write_config", lambda path: True)
    monkeypatch.setattr(setup_wizard, "save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard, "save_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_wizard, "capabilities_report_path", lambda: Path("/tmp/voxera-cap.json")
    )
    monkeypatch.setattr(
        setup_wizard,
        "_ensure_runtime_services_running",
        lambda: {"failed": [], "started": []},
    )
    monkeypatch.setattr(setup_wizard, "_launch_choice", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard, "_print_what_next", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    warn_checks = [
        {"check": "brain readiness", "status": "warn", "detail": "missing", "hint": "fix"}
    ]
    monkeypatch.setattr(setup_wizard, "_check_brain_config", lambda cfg: list(warn_checks))
    monkeypatch.setattr(setup_wizard, "run_quick_doctor", lambda: [])

    cfg = asyncio.run(setup_wizard.run_setup())

    assert isinstance(cfg, AppConfig)


# --- Next-steps output tests ---


def test_print_what_next_default_shows_three_compact_steps(capsys):
    """Default next-steps output includes exactly the 3 intended commands."""
    setup_wizard._print_what_next()
    out = capsys.readouterr().out

    assert "voxera doctor --quick" in out
    assert "voxera vera" in out
    assert "voxera panel" in out
    assert "Three things to try" in out


def test_print_what_next_default_excludes_old_command_dump(capsys):
    """Default output no longer includes the large old command list."""
    setup_wizard._print_what_next()
    out = capsys.readouterr().out

    assert "voxera demo" not in out
    assert "voxera queue status" not in out
    assert "voxera queue reconcile" not in out
    assert "voxera queue prune" not in out
    assert "voxera artifacts prune" not in out
    assert "single-writer lock" not in out
    assert "Next Steps (verbose)" not in out


def test_print_what_next_default_has_explanatory_text(capsys):
    """Each of the 3 commands has a concise explanation."""
    setup_wizard._print_what_next()
    out = capsys.readouterr().out

    assert "check config and connectivity" in out
    assert "start a conversation with Vera" in out
    assert "open the operator dashboard" in out


def test_print_what_next_default_mentions_verbose_path(capsys):
    """Default output tells the user how to see the full command list."""
    setup_wizard._print_what_next()
    out = capsys.readouterr().out

    assert "--verbose-next" in out


def test_print_what_next_verbose_shows_full_command_list(capsys):
    """Verbose path shows the full command list including advanced commands."""
    setup_wizard._print_what_next(verbose=True)
    out = capsys.readouterr().out

    assert "voxera doctor --quick" in out
    assert "voxera doctor --self-test" in out
    assert "voxera vera" in out
    assert "voxera panel" in out
    assert "voxera demo" in out
    assert "voxera demo --online" in out
    assert "voxera queue status" in out
    assert "voxera queue reconcile" in out
    assert "voxera queue reconcile --fix" in out
    assert "voxera queue prune" in out
    assert "voxera artifacts prune" in out
    assert "verbose" in out.lower()


def test_print_what_next_verbose_excludes_compact_framing(capsys):
    """Verbose output does not show the compact 'Three things to try' framing."""
    setup_wizard._print_what_next(verbose=True)
    out = capsys.readouterr().out

    assert "Three things to try" not in out


def test_print_what_next_default_output_is_compact(capsys):
    """Default output fits comfortably in one screen (under 15 lines)."""
    setup_wizard._print_what_next()
    out = capsys.readouterr().out

    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) <= 15


def test_run_setup_passes_verbose_next_flag(monkeypatch):
    """run_setup forwards verbose_next to _print_what_next."""
    import asyncio

    monkeypatch.setattr(setup_wizard, "ensure_dirs", lambda: None)
    monkeypatch.setattr(setup_wizard, "_pick_mode", lambda: "mixed")
    monkeypatch.setattr(setup_wizard, "_pick_brain_type", lambda: "cloud")
    monkeypatch.setattr(setup_wizard, "_configure_cloud_brains", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_configure_web_investigation", lambda cfg: None)
    monkeypatch.setattr(setup_wizard, "_policy_defaults", lambda: setup_wizard.PolicyApprovals())
    monkeypatch.setattr(setup_wizard, "_confirm_write_config", lambda path: True)
    monkeypatch.setattr(setup_wizard, "save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard, "save_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_wizard, "capabilities_report_path", lambda: Path("/tmp/voxera-cap.json")
    )
    monkeypatch.setattr(
        setup_wizard,
        "_post_setup_validation",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        setup_wizard,
        "_ensure_runtime_services_running",
        lambda: {"failed": [], "started": []},
    )
    monkeypatch.setattr(setup_wizard, "_launch_choice", lambda **kwargs: None)
    monkeypatch.setattr(setup_wizard.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    captured_kwargs: list[dict] = []

    def _capture_what_next(**kwargs):
        captured_kwargs.append(kwargs)

    monkeypatch.setattr(setup_wizard, "_print_what_next", _capture_what_next)

    asyncio.run(setup_wizard.run_setup(verbose_next=True))

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["verbose"] is True
